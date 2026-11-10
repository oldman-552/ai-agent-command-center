"""Automation loop: the worker's `automations` role, one active leader at a time (PostgreSQL advisory lock)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import text

from app.automation import engine
from app.db import get_engine, session_factory

log = logging.getLogger("automations")
LEADER_KEY = 0x4A070A11
INTERVAL_SECONDS = 30


async def run(stop: asyncio.Event) -> None:
    conn = get_engine().connect()
    try:
        while not stop.is_set():
            leader = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_KEY}).scalar()
            conn.commit()
            if leader:
                with session_factory()() as db:
                    try:
                        engine.tick(db)
                        db.commit()
                    except Exception:
                        db.rollback()
                        log.exception("automation tick failed")
            try:
                await asyncio.wait_for(stop.wait(), INTERVAL_SECONDS)
            except TimeoutError:
                pass
    finally:
        conn.close()
