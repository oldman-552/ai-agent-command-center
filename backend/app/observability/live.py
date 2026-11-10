"""Real-time updates (UX-02, FND-14, ADR-028).

Changes are published to Redis **after the database transaction commits** (never for rolled-back work) on the
channel `acc:live:<organization_id>`; the API's WebSocket endpoint relays them to signed-in browsers of that
organization, which refresh the affected views. Messages carry only a topic and ids — never content — so a
subscriber learns nothing it could not read through the API with its own permissions. Redis being down never breaks
a write: publishing is best-effort and logged.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import time
from typing import Any

import redis
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import get_settings

log = logging.getLogger("live")
_client: redis.Redis | None = None
HEARTBEAT_TTL = 45


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(get_settings().redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
    return _client


def channel(organization_id: int) -> str:
    return f"acc:live:{organization_id}"


def notify(db: Session, organization_id: int | None, topic: str, **ids: Any) -> None:
    """Queue a live update for after the commit of `db`."""
    if organization_id is None:
        return
    db.info.setdefault("live", []).append((organization_id, {"topic": topic, **{k: v for k, v in ids.items()
                                                                              if v is not None}}))


@event.listens_for(Session, "after_commit")
def _publish(session: Session) -> None:
    pending = session.info.pop("live", None)
    if not pending:
        return
    try:
        pipe = client().pipeline(transaction=False)
        seen = set()
        for org, msg in pending:
            key = (org, json.dumps(msg, sort_keys=True, default=str))
            if key in seen:  # one message per distinct change per transaction
                continue
            seen.add(key)
            pipe.publish(channel(org), key[1])
        pipe.execute()
    except Exception as e:  # noqa: BLE001 — best effort
        log.warning("live update not published: %s", e)


@event.listens_for(Session, "after_rollback")
def _drop(session: Session) -> None:
    session.info.pop("live", None)


# ---------------- service heartbeats (health of all services) ----------------
def heartbeat(service: str, details: dict[str, Any] | None = None) -> None:
    try:
        key = f"acc:heartbeat:{service}:{socket.gethostname()}:{os.getpid()}"
        client().set(key, json.dumps({"service": service, "host": socket.gethostname(), "pid": os.getpid(),
                                      "at": time.time(), **(details or {})}), ex=HEARTBEAT_TTL)
    except Exception as e:  # noqa: BLE001
        log.warning("heartbeat failed: %s", e)


def heartbeats() -> list[dict[str, Any]]:
    out = []
    for key in client().scan_iter("acc:heartbeat:*", count=200):
        raw = client().get(key)
        if raw:
            out.append(json.loads(raw))
    return sorted(out, key=lambda x: (x["service"], x["host"], x["pid"]))
