"""Bridge health monitor — part of the worker's `workforce` role, one leader at a time (PostgreSQL advisory lock),
like the supervisor.

Every interval it takes one reading of each enabled bridge (`/health`, the key probe, the session registry) and
stores the masked snapshot, so the pages and the Manager see whether a worker's account is still there without
anyone pressing a button. It never sends a chat request, so nothing reaches Arena from here.
"""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from app.config import get_settings
from app.connectivity.arena_bridge import service
from app.connectivity.arena_bridge.adapter import KIND
from app.db import get_engine, session_factory
from app.models import Provider

log = logging.getLogger("workforce.bridge")
LEADER_KEY = 0x0A7E4B1D  # arbitrary constant for pg_try_advisory_lock; distinct from the supervisor's


async def poll_once() -> int:
    """Read every enabled bridge once. Returns how many readings were stored."""
    with session_factory()() as db:
        ids = list(db.scalars(select(Provider.id).where(Provider.kind == KIND, Provider.enabled.is_(True))))
    stored = 0
    for pid in ids:
        with session_factory()() as db:
            p = db.get(Provider, pid)
            if p is None or not p.enabled:
                continue
            try:
                await service.refresh_health(db, p)
                db.commit()
                stored += 1
            except Exception:
                db.rollback()
                log.exception("health reading failed for bridge %s", pid)
    return stored


async def run(stop: asyncio.Event) -> None:
    conn = get_engine().connect()  # holds the leadership lock for this process's lifetime
    try:
        while not stop.is_set():
            leader = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_KEY}).scalar()
            conn.commit()
            if leader:
                try:
                    await poll_once()
                except Exception:
                    log.exception("bridge health poll failed")
            try:
                await asyncio.wait_for(stop.wait(), max(5, get_settings().arena_bridge_health_interval_seconds))
            except TimeoutError:
                pass
    finally:
        conn.close()
