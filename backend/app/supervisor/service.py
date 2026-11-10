"""Supervisor loop: a separate worker role, one active leader at a time (PostgreSQL advisory lock)."""
from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, text

from app.config import get_settings
from app.db import get_engine, session_factory
from app.models import Organization
from app.security.audit import log_activity
from app.supervisor import engine

log = logging.getLogger("supervisor")
LEADER_KEY = 0x5E7E7150  # arbitrary constant for pg_try_advisory_lock


async def run(stop: asyncio.Event) -> None:
    conn = get_engine().connect()  # holds the leadership lock for this process's lifetime
    try:
        while not stop.is_set():
            leader = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LEADER_KEY}).scalar()
            conn.commit()
            if leader:
                with session_factory()() as db:
                    for org in db.scalars(select(Organization.id)).all():
                        try:
                            r = engine.sweep(db, org)
                            log_activity(db, "supervisor.sweep", category="supervisor", organization_id=org,
                                         actor="supervisor", details={"findings": r["findings"],
                                                                      "actions": len(r["actions"])})
                            db.commit()
                        except Exception:
                            db.rollback()
                            log.exception("supervisor sweep failed for organization %s", org)
            try:
                await asyncio.wait_for(stop.wait(), get_settings().supervisor_interval_seconds)
            except TimeoutError:
                pass
    finally:
        conn.close()
