"""Arena Local Bridge instances: settings, terms gate, diagnostics, health snapshots, account binding (ADR-032).

A bridge instance is a `Provider` of kind `arena_local_bridge`. Its options live in `providers.settings`, the last
health snapshot in `providers.health`. Account e-mail addresses returned by the bridge are masked before they are
stored or shown; a salted hash is kept only to tell accounts apart.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.connectivity.arena_bridge.adapter import KIND
from app.connectivity.arena_bridge.client import (
    PROBE_MODELS,
    ArenaBridgeClient,
    BridgeConfig,
    BridgeError,
    account_hash,
    host_allowed,
    mask_email,
    mask_emails,
    normalize_base_url,
)
from app.models import ApiCredential, ModelInfo, Provider
from app.security.audit import log_activity
from app.security.crypto import decrypt_secret, encrypt_secret, redact, secret_hint

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
DEFAULT_URL = "http://host.docker.internal:20140"
TERMS_URL = "https://help.arena.ai/articles/5629909088-terms-of-use"
TERMS_STATEMENT = (
    "I have read Arena's terms of use ({url}). I understand that Arena Local Bridge is not an official Arena API: it "
    "drives Arena's web application with the accounts signed in to it, and Arena's terms may prohibit automated "
    "access. I will use only my own accounts, I will not send confidential code or personal data, and I accept "
    "responsibility for this use. The platform adds no way around sign-in, CAPTCHA, rate limits or quotas, and this "
    "acknowledgement applies to the bridge at {address}.")
SETTING_FIELDS = {"host_header": str, "stream": bool, "connect_timeout_s": float, "idle_timeout_s": float,
                  "request_timeout_s": float, "replay_window_s": float, "read_session_registry": bool,
                  "max_prompt_chars": int, "max_concurrent": int}
HEALTH_FRESH_SECONDS = 90


class BridgeConfigError(ValueError):
    """A machine-readable configuration problem (the API turns it into 422)."""


def is_bridge(p: Provider | None) -> bool:
    return p is not None and p.kind == KIND


def extra_hosts() -> frozenset[str]:
    return frozenset(h.strip().lower() for h in get_settings().arena_bridge_allowed_hosts.split(",") if h.strip())


def validate_base_url(url: str) -> str:
    try:
        base = normalize_base_url(url)
    except ValueError as e:
        raise BridgeConfigError("invalid_url") from e
    if not host_allowed(urlsplit(base).hostname or "", extra_hosts()):
        raise BridgeConfigError("host_not_allowed")
    return base


def clean_settings(patch: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    out = dict(current or {})
    for k, v in patch.items():
        if k not in SETTING_FIELDS:
            raise BridgeConfigError(f"unknown_setting:{k}")
        kind = SETTING_FIELDS[k]
        if kind is bool:
            if not isinstance(v, bool):
                raise BridgeConfigError(f"invalid_setting:{k}")
            out[k] = v
        elif kind is str:
            v = str(v or "").strip()
            if v and not re.fullmatch(r"(127\.0\.0\.1|localhost|\[::1\])(:\d{1,5})?", v):
                raise BridgeConfigError("invalid_host_header")  # the bridge only accepts a loopback Host anyway
            out[k] = v
        else:
            try:
                out[k] = kind(v)
            except (TypeError, ValueError) as e:
                raise BridgeConfigError(f"invalid_setting:{k}") from e
            if out[k] <= 0 and k != "replay_window_s":
                raise BridgeConfigError(f"invalid_setting:{k}")
    return out


def config_of(p: Provider) -> BridgeConfig:
    return BridgeConfig.from_settings(p.base_url or "", p.settings)


def client_for(p: Provider) -> ArenaBridgeClient:
    from app.connectivity.gateway.service import HTTP_CLIENTS
    key = decrypt_secret(p.credential.encrypted_value) if p.credential else None
    return ArenaBridgeClient(config_of(p), key, HTTP_CLIENTS.get(KIND))


def _salt(organization_id: int) -> str:
    return hashlib.sha256(f"{get_settings().app_encryption_key}|arena-accounts|{organization_id}".encode()).hexdigest()


def _set_settings(p: Provider, settings: dict[str, Any]) -> None:
    p.settings = settings
    flag_modified(p, "settings")


# ---------------- instances ----------------
def set_key(db: Session, p: Provider, user_id: int, key: str) -> None:
    cred = ApiCredential(organization_id=p.organization_id, name=f"{p.name} bridge key", kind=KIND,
                         encrypted_value=encrypt_secret(key), hint=secret_hint(key), created_by_user_id=user_id)
    old = p.credential
    p.credential = cred
    db.flush()
    if old is not None:
        db.delete(old)
        cred.rotated_at = datetime.now(UTC)
    p.status, p.last_error = "untested", None


def create(db: Session, *, organization_id: int, user_id: int, name: str, base_url: str, key: str,
           settings: dict[str, Any] | None = None) -> Provider:
    base = validate_base_url(base_url)
    if db.scalar(select(Provider.id).where(Provider.organization_id == organization_id, Provider.name == name)):
        raise BridgeConfigError("name_taken")
    p = Provider(organization_id=organization_id, name=name, kind=KIND, is_mock=False, base_url=base,
                 auth_type="api_key", status="untested", priority=5, settings=clean_settings(settings or {}))
    db.add(p)
    db.flush()
    set_key(db, p, user_id, key)
    log_activity(db, "workforce.bridge_created", category="workforce", organization_id=organization_id,
                 user_id=user_id, details={"provider_id": p.id, "base_url": base})
    return p


def update(db: Session, p: Provider, user_id: int, *, name: str | None = None, base_url: str | None = None,
           enabled: bool | None = None, settings: dict[str, Any] | None = None) -> Provider:
    changed: dict[str, Any] = {}
    if name is not None and name != p.name:
        if db.scalar(select(Provider.id).where(Provider.organization_id == p.organization_id, Provider.name == name,
                                               Provider.id != p.id)):
            raise BridgeConfigError("name_taken")
        p.name = changed["name"] = name
    if base_url is not None:
        base = validate_base_url(base_url)
        if base != p.base_url:
            p.base_url = changed["base_url"] = base
            # the acknowledgement names the address: a different bridge needs a new one
            s = dict(p.settings or {})
            if s.pop("terms_ack", None) is not None:
                changed["terms_ack"] = "revoked (address changed)"
            _set_settings(p, s)
            p.status, p.health, p.is_mock = "untested", None, False
    if enabled is not None and enabled != p.enabled:
        p.enabled = changed["enabled"] = enabled
    if settings:
        keep = {k: v for k, v in (p.settings or {}).items() if k not in SETTING_FIELDS}
        merged = clean_settings(settings, {k: v for k, v in (p.settings or {}).items() if k in SETTING_FIELDS})
        _set_settings(p, {**keep, **merged})
        changed["settings"] = sorted(settings)
    if changed:
        log_activity(db, "workforce.bridge_updated", category="workforce", organization_id=p.organization_id,
                     user_id=user_id, details={"provider_id": p.id, "changed": changed})
    return p


# ---------------- terms gate ----------------
def statement(p: Provider) -> str:
    return TERMS_STATEMENT.format(url=TERMS_URL, address=p.base_url)


def acknowledge(db: Session, p: Provider, user_id: int, accepted: str) -> None:
    if accepted.strip() != statement(p):
        raise BridgeConfigError("statement_mismatch")  # the person must accept the exact statement shown
    s = dict(p.settings or {})
    s["terms_ack"] = {"by": f"user:{user_id}", "at": datetime.now(UTC).isoformat(), "terms_url": TERMS_URL,
                      "address": p.base_url,
                      "statement_sha256": hashlib.sha256(accepted.strip().encode()).hexdigest()}
    _set_settings(p, s)
    log_activity(db, "workforce.bridge_terms_acknowledged", category="security", level="warning",
                 organization_id=p.organization_id, user_id=user_id,
                 details={"provider_id": p.id, "address": p.base_url, "terms_url": TERMS_URL})


def revoke(db: Session, p: Provider, user_id: int) -> None:
    s = dict(p.settings or {})
    if s.pop("terms_ack", None) is not None:
        _set_settings(p, s)
        log_activity(db, "workforce.bridge_terms_revoked", category="security", organization_id=p.organization_id,
                     user_id=user_id, details={"provider_id": p.id})


def acknowledged(p: Provider) -> bool:
    return bool((p.settings or {}).get("terms_ack"))


# ---------------- health ----------------
def _accounts(health: dict[str, Any], salt: str) -> list[dict[str, Any]]:
    out = []
    for a in health.get("accounts") or []:
        if not isinstance(a, dict) or not a.get("email"):
            continue
        out.append({"account": mask_email(a["email"]), "hash": account_hash(a["email"], salt),
                    "priority": a.get("priority"), "disabled": bool(a.get("disabled")),
                    "error": mask_emails(redact(str(a["lastError"])))[:300] if a.get("lastError") else None,
                    "cookie_expiry_s": a.get("cookieExpirySeconds")})
    return sorted(out, key=lambda a: (a["priority"] is None, a["priority"] or 0))


async def read_health(p: Provider) -> dict[str, Any]:
    """One live reading: `/health`, the key (the bridge's own `ping` probe — no Arena request) and, when allowed,
    the session registry. Never raises; never contains an e-mail address or the key."""
    salt = _salt(p.organization_id)
    snap: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "reachable": False, "ok": False,
                            "ready": False, "key_ok": None, "service": None, "version": None, "mock": False,
                            "queue": None, "accounts": [], "sessions": [], "sessions_read": False, "stats": None,
                            "error": None}
    client = None
    try:
        client = client_for(p)
        h = await client.health()
        stats = h.get("stats") or {}
        snap.update({
            "reachable": True, "ok": bool(h.get("ok")), "ready": bool(h.get("ready")), "service": h.get("service"),
            "version": h.get("version"), "mock": bool(h.get("_fake")),
            "queue": {"active": (h.get("queue") or {}).get("active"), "depth": (h.get("queue") or {}).get("depth"),
                      "max_depth": (h.get("queue") or {}).get("maxDepth")},
            "accounts": _accounts(h, salt),
            "stats": {"requests": stats.get("requests"), "completed": stats.get("completed"),
                      "errors": stats.get("errors"), "last_success_at": stats.get("lastSuccessAt"),
                      "last_error_at": stats.get("lastErrorAt"), "last_error_type": stats.get("lastErrorType"),
                      "average_latency_ms": stats.get("averageLatencyMs")}})
        try:
            await client.ping()
            snap["key_ok"] = True
        except BridgeError as e:
            # `/health` needs no key, so only this probe shows that the stored key is wrong
            snap["key_ok"] = False if e.code == "invalid_key" else None
            snap["error"] = e.as_dict()
        if config_of(p).read_session_registry:
            try:
                rows = await client.sessions()
                snap["sessions"] = [{"session_id": s["session_id"], "model": s["model"], "title": s["title"],
                                     "account": mask_email(s["email"]) if s["email"] else None,
                                     "account_hash": account_hash(s["email"], salt) if s["email"] else None,
                                     "state": s["state"]} for s in rows]
                snap["sessions_read"] = True
            except BridgeError as e:
                snap["sessions_error"] = e.as_dict()
    except BridgeError as e:
        snap["error"] = e.as_dict()
    except ValueError as e:  # undecryptable key, invalid stored address
        snap["error"] = {"code": "configuration", "message": redact(str(e))[:300], "category": "configuration"}
    finally:
        if client is not None:
            await client.aclose()
    return snap


def usable(snap: dict[str, Any] | None) -> bool:
    """Reachable, the key is accepted and an account is ready — what "the bridge works" means."""
    return bool(snap and snap.get("reachable") and snap.get("ready") and snap.get("key_ok") is not False
                and not (snap.get("error") and snap.get("key_ok") is None))


# Called after every stored health reading, in the same transaction: fn(db, provider). The AI Workforce uses it to
# refresh what it knows about the account behind each worker.
ON_HEALTH: list[Callable[[Session, Provider], None]] = []


def apply_health(db: Session, p: Provider, snap: dict[str, Any]) -> None:
    before = (p.health or {}).get("reachable"), (p.health or {}).get("ready"), (p.health or {}).get("key_ok")
    p.health = snap
    flag_modified(p, "health")
    p.last_tested_at = datetime.now(UTC)
    if snap["reachable"]:
        # A fake bridge always identifies itself and the real one never does: everything sent through a fake is
        # labelled mock, and the label goes away only when a real bridge answers at this address.
        p.is_mock = bool(snap.get("mock"))
    if usable(snap):
        p.status, p.last_error = "ok", None
    else:
        err = snap.get("error") or {}
        p.status = "error"
        p.last_error = (f"{err.get('code')}: {err.get('message')}" if err
                        else "no Arena account is ready on the bridge")[:500]
    if before != (snap["reachable"], snap["ready"], snap["key_ok"]):
        log_activity(db, "workforce.bridge_health_changed", category="workforce",
                     level="info" if p.status == "ok" else "warning", organization_id=p.organization_id,
                     details={"provider_id": p.id, "reachable": snap["reachable"], "ready": snap["ready"],
                              "key_ok": snap["key_ok"], "error": (snap.get("error") or {}).get("code")})
    for fn in ON_HEALTH:
        fn(db, p)


async def refresh_health(db: Session, p: Provider) -> dict[str, Any]:
    snap = await read_health(p)
    apply_health(db, p, snap)
    return snap


def health_is_fresh(p: Provider, max_age: float = HEALTH_FRESH_SECONDS) -> bool:
    at = (p.health or {}).get("checked_at")
    if not at:
        return False
    try:
        return (datetime.now(UTC) - datetime.fromisoformat(at)).total_seconds() <= max_age
    except ValueError:
        return False


# ---------------- account binding ----------------
def classify_binding(health: dict[str, Any] | None, model: str | None) -> dict[str, Any]:
    """Which Arena account stands behind a worker, as far as the bridge lets anyone know.

    The bridge has no account selector and names no account in its responses, so every answer here is `attested:
    False`. `deterministic_*` means the bridge's own rules leave only one account; `pooled` means they do not.
    """
    base = {"attested": False, "checked_at": (health or {}).get("checked_at")}
    if not health or not health.get("reachable"):
        return {**base, "type": "unknown", "reason": "bridge_unreachable"}
    enabled = [a for a in health.get("accounts") or [] if not a.get("disabled")]
    model = (model or "").strip()
    if UUID_RE.match(model):
        if not health.get("sessions_read"):
            return {**base, "type": "unknown", "reason": "session_registry_not_read", "session": model}
        sess = next((s for s in health.get("sessions") or [] if s["session_id"].lower() == model.lower()), None)
        if sess is None:
            return {**base, "type": "unknown", "reason": "session_not_found", "session": model}
        owner = next((a for a in health.get("accounts") or [] if a["hash"] == sess.get("account_hash")), None)
        return {**base, "type": "deterministic_session", "session": model, "session_state": sess.get("state"),
                "session_model": sess.get("model"), "account": sess.get("account"),
                "account_hash": sess.get("account_hash"),
                "account_usable": bool(owner and not owner["disabled"]),
                "source": "bridge session registry (the owner of a session is always used)"}
    if model == "active" or not model:
        return {**base, "type": "pooled", "reason": "session_selected_in_bridge_gui",
                "accounts": len(enabled)}
    if len(enabled) == 1:
        return {**base, "type": "deterministic_single_account", "account": enabled[0]["account"],
                "account_hash": enabled[0]["hash"], "account_usable": True,
                "source": "the bridge has exactly one enabled account"}
    if not enabled:
        return {**base, "type": "unknown", "reason": "no_enabled_account"}
    return {**base, "type": "pooled", "reason": "several_accounts_no_selector", "accounts": len(enabled),
            "primary_account": enabled[0]["account"], "primary_account_hash": enabled[0]["hash"]}


# ---------------- discovery ----------------
async def discover(db: Session, p: Provider) -> dict[str, Any]:
    """Sessions the bridge offers as `model` values, with their owner (masked) when the registry may be read."""
    client = client_for(p)
    try:
        models = [m for m in await client.models() if m["id"] not in PROBE_MODELS]
    finally:
        await client.aclose()
    snap = await refresh_health(db, p)
    owners = {s["session_id"].lower(): s for s in snap.get("sessions") or []}
    existing = {m.name: m for m in p.models}
    added = 0
    out = []
    for m in models:
        info = owners.get(m["id"].lower(), {})
        label = " — ".join(x for x in (m["name"] or info.get("model") or m["owned_by"], info.get("account")) if x)
        if m["id"] in existing:
            existing[m["id"]].display_name = label[:128] or existing[m["id"]].display_name
        else:
            p.models.append(ModelInfo(name=m["id"], display_name=label[:128] or None, discovered=True))
            added += 1
        out.append({"id": m["id"], "kind": "session" if UUID_RE.match(m["id"]) else "flow", "label": label,
                    "model": m["name"] or info.get("model"), "account": info.get("account"),
                    "account_hash": info.get("account_hash"), "state": info.get("state")})
    return {"discovered": len(models), "added": added, "models": out, "mock": bool(snap.get("mock"))}


# ---------------- diagnostics ----------------
def in_container() -> bool:
    return os.path.exists("/.dockerenv") or bool(os.environ.get("ACC_IN_DOCKER"))


async def diagnose(db: Session, p: Provider) -> dict[str, Any]:
    """Step-by-step check from where the platform really runs (inside the API container in Docker deployments).
    Each step names what was tried, what happened and what to do next; the first failed connection step ends the
    walk. Nothing is sent to Arena. The stored health reading is refreshed whatever the outcome."""
    cfg = config_of(p)
    u = urlsplit(cfg.base_url)
    host, port = u.hostname or "", u.port or 80
    docker = in_container()
    steps: list[dict[str, Any]] = []

    def step(name: str, ok: bool | None, detail: str, hint: str | None = None, **extra: Any) -> bool:
        steps.append({"step": name, "ok": ok, "detail": detail, "hint": hint, **extra})
        return bool(ok)

    async def walk() -> None:
        step("address", True, f"{cfg.base_url} — Host header sent: {cfg.effective_host_header or f'{host}:{port}'}")
        if docker and host in ("127.0.0.1", "localhost", "::1"):
            step("container_localhost", None,
                 "the platform runs in a container, where localhost is the container itself, not the Windows host",
                 "use http://host.docker.internal:20140 as the address")
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 5)
            step("dns", True, f"{host} resolves to {', '.join(sorted({i[4][0] for i in infos}))}")
        except (OSError, TimeoutError) as e:
            step("dns", False, f"{host} does not resolve ({type(e).__name__})",
                 "Docker Desktop provides host.docker.internal; on Linux Docker Engine add "
                 "extra_hosts: [\"host.docker.internal:host-gateway\"] (the shipped compose file does)")
            return
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), cfg.connect_timeout)
            writer.close()
            await writer.wait_closed()
            step("tcp", True, f"port {port} accepts connections")
        except (OSError, TimeoutError) as e:
            step("tcp", False, f"cannot connect to {host}:{port} ({type(e).__name__})",
                 "start Arena Local Bridge on the host (ArenaLocalBridge.exe or start-gui.bat) and check its port. "
                 "If it runs and this still fails, the host does not forward this address to a service bound to "
                 "127.0.0.1 — see docs/ARENA_LOCAL_BRIDGE.md, \"Networking\"")
            return
        client = client_for(p)
        try:
            try:
                h = await client.health()
                step("health", True, f"{h.get('service') or 'bridge'} {h.get('version')}"
                     + (" — FAKE bridge (tests only)" if h.get("_fake") else ""), version=h.get("version"))
            except BridgeError as e:
                hint = {"host_guard": "set the Host header to 127.0.0.1:<port> (leave the field empty to derive it)",
                        "not_a_bridge": "another program answers on this port; check the address"}.get(e.code)
                step("health", False, f"{e.code}: {e.message}", hint, code=e.code)
                return
            try:
                await client.ping()
                step("key", True, "the bridge accepted the key (probe model ping answered pong; no Arena request)")
            except BridgeError as e:
                step("key", False, f"{e.code}: {e.message}",
                     "enter the ARENA_AGENT_BRIDGE_KEY shown by the bridge (GUI → connection, or its .env file)"
                     if e.code == "invalid_key" else None, code=e.code)
                return
            ready, body = await client.ready()
            accounts = _accounts(body, _salt(p.organization_id))
            enabled = [a for a in accounts if not a["disabled"]]
            step("ready", ready, f"{len(enabled)} enabled account(s) of {len(accounts)}; ready: {ready}",
                 None if ready else "sign in an Arena account in the bridge (its GUI) and wait until it is verified",
                 accounts=accounts)
            try:
                models = [m for m in await client.models() if m["id"] not in PROBE_MODELS]
                sessions = [m for m in models if UUID_RE.match(m["id"])]
                step("models", True, f"{len(sessions)} session(s)"
                     + ("" if sessions else "; no archived session — create one per account in the bridge and pick "
                                            "it for each worker"), count=len(models))
            except BridgeError as e:
                step("models", False, f"{e.code}: {e.message}", code=e.code)
            if cfg.read_session_registry:
                try:
                    rows = await client.sessions()
                    owners = {account_hash(r["email"], _salt(p.organization_id)) for r in rows if r["email"]}
                    step("session_registry", True, f"{len(rows)} session(s) owned by {len(owners)} account(s)",
                         distinct_accounts=len(owners))
                except BridgeError as e:
                    step("session_registry", False, f"{e.code}: {e.message}", code=e.code)
            step("terms", acknowledged(p), "acknowledged" if acknowledged(p) else "not acknowledged yet",
                 None if acknowledged(p) else "nothing is sent to Arena until a person acknowledges the terms below")
        except BridgeError as e:  # a later step lost the connection
            step("connection", False, f"{e.code}: {e.message}", code=e.code)
        finally:
            await client.aclose()

    await walk()
    snap = await refresh_health(db, p)
    done = {s["step"]: s["ok"] for s in steps}
    return {"ok": all(done.get(k) is True for k in ("dns", "tcp", "health", "key")),
            "ready": bool(snap.get("ready")), "mock": bool(snap.get("mock")), "steps": steps,
            "checked_at": datetime.now(UTC).isoformat(),
            "environment": {"in_container": docker, "base_url": cfg.base_url,
                            "host_header": cfg.effective_host_header or f"{host}:{port}",
                            "host_header_overridden": cfg.effective_host_header is not None,
                            "streaming": cfg.stream}}
