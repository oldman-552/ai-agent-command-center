"""WebSocket endpoint for live updates (UX-02) and the all-services health report (UX-05)."""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.config import get_settings
from app.db import session_factory
from app.models import User
from app.observability import live
from app.security.auth import COOKIE_NAME, CSRF_COOKIE
from app.security.crypto import decode_access_token

router = APIRouter(tags=["realtime"])


def _user_from_cookie(ws: WebSocket) -> User | None:
    data = decode_access_token(ws.cookies.get(COOKIE_NAME) or "")
    if not data:
        return None
    with session_factory()() as db:
        u = db.get(User, int(data["sub"]))
        if not u or not u.is_active or u.token_version != data.get("tv"):
            return None
        db.expunge(u)
        return u


@router.websocket("/api/ws")
async def ws_live(ws: WebSocket) -> None:
    """Session-cookie authenticated. The first client message must echo the CSRF cookie (cross-site WebSocket
    hijacking protection). Then the socket receives `{"topic": ..., ids}` for the user's organization."""
    user = _user_from_cookie(ws)
    if user is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
    except Exception:  # noqa: BLE001
        await ws.close(code=4403)
        return
    cookie = ws.cookies.get(CSRF_COOKIE) or ""
    if not cookie or not secrets.compare_digest(str(first.get("csrf", "")), cookie):
        await ws.close(code=4403)
        return
    r = aioredis.Redis.from_url(get_settings().redis_url)
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(live.channel(user.organization_id))
        await ws.send_json({"topic": "hello", "organization_id": user.organization_id})

        async def pump() -> None:
            async for m in pubsub.listen():
                if m.get("type") == "message":
                    await ws.send_text(m["data"].decode() if isinstance(m["data"], bytes) else str(m["data"]))

        async def drain() -> None:  # detect the client going away; ignore anything it sends
            while True:
                await ws.receive_text()

        done, pending = await asyncio.wait({asyncio.create_task(pump()), asyncio.create_task(drain())},
                                           return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
                t.result()
    except Exception:  # noqa: BLE001 — Redis down: tell the client to fall back to polling
        with contextlib.suppress(Exception):
            await ws.close(code=1011)
    finally:
        with contextlib.suppress(Exception):
            await pubsub.aclose()
            await r.aclose()


# ---------------- health of all services ----------------
async def _rabbitmq() -> str:
    try:
        import aio_pika
        conn = await asyncio.wait_for(aio_pika.connect(get_settings().rabbitmq_url), 3)
        await conn.close()
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


async def _bridge() -> dict[str, Any]:
    from app.browser import client
    from app.browser.bridge import BridgeError
    try:
        h = await asyncio.wait_for(client.call("GET", "/health"), 3)
        return {"status": "ok", "live_sessions": h.get("live_sessions")}
    except (BridgeError, TimeoutError):
        return {"status": "not_running"}  # optional, experimental service


def _arena_bridges() -> dict[str, Any]:
    """Arena Local Bridge, from the readings the platform stored (no address, no account): this route needs no
    sign-in, so it only says how many bridges are configured and how many were ready when last checked."""
    from sqlalchemy import select

    from app.connectivity.arena_bridge import service as bridge
    from app.models import Provider
    try:
        with session_factory()() as s:
            rows = list(s.scalars(select(Provider).where(Provider.kind == bridge.KIND, Provider.enabled.is_(True))))
    except Exception:  # noqa: BLE001
        return {"status": "unknown"}
    if not rows:
        return {"status": "not_configured", "configured": 0}  # optional: Human-Assisted mode needs no bridge
    fresh = [p for p in rows if bridge.health_is_fresh(p, 300)]
    ready = [p for p in fresh if (p.health or {}).get("ready")]
    reachable = [p for p in fresh if (p.health or {}).get("reachable")]
    state = "ok" if len(ready) == len(rows) else "degraded" if reachable else "unreachable" if fresh else "unknown"
    return {"status": state, "configured": len(rows), "reachable": len(reachable), "ready": len(ready),
            "mock": sum(1 for p in rows if p.is_mock)}


async def health_report() -> tuple[int, dict[str, Any]]:
    db = "ok"
    try:
        with session_factory()() as s:
            s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db = "error"
    try:
        redis_state = "ok" if live.client().ping() else "error"
        beats = live.heartbeats()
    except Exception:  # noqa: BLE001
        redis_state, beats = "error", []
    rabbit, bridge = await asyncio.gather(_rabbitmq(), _bridge())
    workers = [b for b in beats if b["service"] == "worker"]
    services = {"database": db, "rabbitmq": rabbit, "redis": redis_state,
                "workers": {"status": "ok" if workers else "none", "instances": workers},
                "browser_bridge": bridge,
                "arena_local_bridge": _arena_bridges() if db == "ok" else {"status": "unknown"}}
    core_ok = db == "ok" and rabbit == "ok" and redis_state == "ok"
    body = {"status": "ok" if core_ok and workers else "degraded", "database": db, "services": services}
    return (200 if db == "ok" else 503), body
