"""A2A push notifications (spec §3.1.7–3.1.10, §4.3, §13.2) for tasks the platform serves to external clients.

- Configs belong to one task and to the access key that created it; token and credentials are encrypted at rest and
  never returned (write-only, like every other secret in the platform).
- Every status change of such a task writes a delivery row in the same transaction (transactional outbox), with a
  StreamResponse payload: `statusUpdate` while it runs, the full `task` (status + artifacts) once it is final.
- A worker loop POSTs due deliveries (`Content-Type: application/a2a+json`, `Authorization: <scheme> <credentials>`,
  the config's token as `X-A2A-Notification-Token`), 10 s timeout, no redirects, exponential backoff; a config is
  disabled after MAX_CONSECUTIVE_FAILURES.
- SSRF (§13.2): HTTPS only, no credentials in the URL, and the host must resolve only to public addresses — checked
  when the config is created and again before every delivery. `A2A_PUSH_ALLOW_PRIVATE=true` relaxes this for local
  testing only.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import session_factory
from app.models import A2APushConfig, A2APushDelivery, Execution
from app.security.crypto import decrypt_secret, encrypt_secret

log = logging.getLogger(__name__)

MAX_CONFIGS_PER_TASK = 10
MAX_CONSECUTIVE_FAILURES = 8
MAX_ATTEMPTS = 8
TIMEOUT_S = 10.0
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 600.0
SCHEMES = {"bearer", "basic", "apikey"}  # IANA names, case-insensitive (RFC 9110 §11.1)


class PushConfigError(ValueError):
    pass


def _allow_private() -> bool:
    return bool(getattr(get_settings(), "a2a_push_allow_private", False))


def _public(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def check_url(url: str, *, resolve: bool = True) -> None:
    """Raise PushConfigError unless the webhook URL is acceptable (§13.2)."""
    if len(url) > 2048:
        raise PushConfigError("url is too long")
    try:
        u = urlsplit(url)
        port = u.port
    except ValueError as e:
        raise PushConfigError("url is not valid") from e
    allow_private = _allow_private()
    if u.scheme != "https" and not (allow_private and u.scheme == "http"):
        raise PushConfigError("url must use https")
    if not u.hostname or u.username or u.password:
        raise PushConfigError("url must have a host and no credentials")
    if allow_private or not resolve:
        return
    host = u.hostname
    if host.lower() in ("localhost",) or host.lower().endswith((".localhost", ".local", ".internal")):
        raise PushConfigError("url must not point to a local host")
    try:
        infos = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise PushConfigError("url host does not resolve") from e
    addrs = {i[4][0] for i in infos}
    if not addrs or not all(_public(a) for a in addrs):
        raise PushConfigError("url must resolve to public addresses only (private, loopback and link-local are refused)")


def create_config(db: Session, ex: Execution, key_id: int, cfg: dict[str, Any]) -> A2APushConfig:
    url = cfg.get("url")
    if not isinstance(url, str) or not url:
        raise PushConfigError("url is required")
    check_url(url)
    n = len(db.scalars(select(A2APushConfig.id).where(A2APushConfig.execution_id == ex.id)).all())
    if n >= MAX_CONFIGS_PER_TASK:
        raise PushConfigError(f"at most {MAX_CONFIGS_PER_TASK} push notification configs per task")
    token = cfg.get("token")
    auth = cfg.get("authentication") or None
    scheme = creds = None
    if auth is not None:
        if not isinstance(auth, dict) or not isinstance(auth.get("scheme"), str):
            raise PushConfigError("authentication.scheme is required")
        scheme = auth["scheme"].strip()
        if scheme.lower() not in SCHEMES:
            raise PushConfigError(f"authentication.scheme must be one of {sorted(SCHEMES)}")
        creds = auth.get("credentials")
        if creds is not None and (not isinstance(creds, str) or len(creds) > 4096 or "\n" in creds or "\r" in creds):
            raise PushConfigError("authentication.credentials is not valid")
    if token is not None and (not isinstance(token, str) or len(token) > 1024 or "\n" in token or "\r" in token):
        raise PushConfigError("token is not valid")
    cid = cfg.get("id") if isinstance(cfg.get("id"), str) and 0 < len(cfg["id"]) <= 64 else str(uuid.uuid4())
    if db.get(A2APushConfig, cid) is not None:
        cid = str(uuid.uuid4())
    row = A2APushConfig(id=cid, execution_id=ex.id, access_key_id=key_id, url=url,
                        token_encrypted=encrypt_secret(token) if token else None, auth_scheme=scheme,
                        credentials_encrypted=encrypt_secret(creds) if creds else None)
    db.add(row)
    db.flush()
    return row


def config_json(c: A2APushConfig) -> dict[str, Any]:
    """The config as the spec's TaskPushNotificationConfig, without its secrets (write-only)."""
    out: dict[str, Any] = {"id": c.id, "taskId": c.execution_id, "url": c.url}
    if c.auth_scheme:
        out["authentication"] = {"scheme": c.auth_scheme}
    meta: dict[str, Any] = {"tokenSet": bool(c.token_encrypted), "consecutiveFailures": c.consecutive_failures}
    if c.disabled_at:
        meta["disabledAt"] = c.disabled_at.isoformat()
    out["metadata"] = meta
    return out


def on_status_change(db: Session, ex: Execution) -> None:
    """Called by the runtime inside the status-change transaction: one delivery per active config."""
    configs = db.scalars(select(A2APushConfig).where(A2APushConfig.execution_id == ex.id,
                                                     A2APushConfig.disabled_at.is_(None))).all()
    if not configs:
        return
    from app.connectivity.a2a.server import is_final, task_json
    task = task_json(ex)
    payload = {"task": task} if is_final(ex) else {
        "statusUpdate": {"taskId": ex.id, "contextId": task["contextId"], "status": task["status"],
                         "metadata": task["metadata"]}}
    now = datetime.now(UTC)
    for c in configs:
        db.add(A2APushDelivery(config_id=c.id, payload=payload, next_attempt_at=now, created_at=now))


def _backoff(attempts: int) -> float:
    return min(BACKOFF_BASE_S * (2 ** max(attempts - 1, 0)), BACKOFF_MAX_S)


async def _post(c: A2APushConfig, payload: dict) -> int:
    await asyncio.to_thread(check_url, c.url)  # again at delivery time: DNS may have changed since creation
    headers = {"Content-Type": "application/a2a+json", "A2A-Version": "1.0"}
    if c.auth_scheme:
        creds = decrypt_secret(c.credentials_encrypted) if c.credentials_encrypted else ""
        headers["Authorization"] = f"{c.auth_scheme} {creds}".strip()
    if c.token_encrypted:
        headers["X-A2A-Notification-Token"] = decrypt_secret(c.token_encrypted)
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False) as h:
        r = await h.post(c.url, json=payload, headers=headers)
    return r.status_code


async def deliver_due(limit: int = 50) -> int:
    """Send due deliveries in order per config. Returns how many succeeded. Safe with several workers."""
    sent = 0
    with session_factory()() as db:
        now = datetime.now(UTC)
        rows = db.scalars(select(A2APushDelivery).where(
            A2APushDelivery.delivered_at.is_(None), A2APushDelivery.abandoned_at.is_(None),
            A2APushDelivery.next_attempt_at <= now).order_by(A2APushDelivery.id)
            .limit(limit).with_for_update(skip_locked=True)).all()
        blocked: set[str] = set()  # keep per-config order: after a failure, later events of that config wait
        for d in rows:
            if d.config_id in blocked:
                continue
            c = db.get(A2APushConfig, d.config_id)
            if c is None or c.disabled_at is not None:
                d.abandoned_at = now
                continue
            d.attempts += 1
            try:
                status = await _post(c, d.payload)
                ok, err = 200 <= status < 300, f"HTTP {status}"
            except Exception as e:  # noqa: BLE001 — refused URL, network, timeout: all retried
                status, ok, err = None, False, f"{type(e).__name__}: {str(e)[:200]}"
            d.last_status = status
            if ok:
                d.delivered_at = datetime.now(UTC)
                c.consecutive_failures, c.last_error, c.last_delivered_at = 0, None, d.delivered_at
                sent += 1
                continue
            blocked.add(c.id)
            c.consecutive_failures += 1
            c.last_error = err[:500]
            if d.attempts >= MAX_ATTEMPTS:
                d.abandoned_at = datetime.now(UTC)
            else:
                d.next_attempt_at = datetime.now(UTC) + timedelta(seconds=_backoff(d.attempts))
            if c.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                c.disabled_at = datetime.now(UTC)
                log.warning("a2a push config %s disabled after %d failures", c.id, c.consecutive_failures)
        db.commit()
    return sent


RETENTION = timedelta(days=7)


def prune(now: datetime | None = None) -> int:
    """Delivered or abandoned rows are kept for a week (troubleshooting), then removed."""
    cutoff = (now or datetime.now(UTC)) - RETENTION
    with session_factory()() as db:
        n = db.execute(delete(A2APushDelivery).where(or_(A2APushDelivery.delivered_at < cutoff,
                                                         A2APushDelivery.abandoned_at < cutoff))).rowcount
        db.commit()
    return n or 0


async def run(stop: asyncio.Event, interval: float = 1.0) -> None:
    """Worker loop (role `a2a_push`)."""
    ticks = 0
    while not stop.is_set():
        try:
            await deliver_due()
            ticks += 1
            if ticks % 3600 == 1:  # about hourly
                await asyncio.to_thread(prune)
        except Exception:
            log.exception("a2a push delivery failed")
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except TimeoutError:
            pass
