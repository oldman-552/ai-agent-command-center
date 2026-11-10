"""Circuit breakers for agents and providers (EV-03, spec Phase 15 "circuit breakers").

Policy failures (a denied tool), cancellations and the user's own mistakes do not count — only failures that say
something about the agent's or provider's health. Every transition is audited.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CircuitBreaker
from app.security.audit import log_activity


def _row(db: Session, scope: str, ref_id: int, organization_id: int) -> CircuitBreaker:
    db.execute(insert(CircuitBreaker).values(organization_id=organization_id, scope=scope, ref_id=ref_id)
               .on_conflict_do_nothing(index_elements=["scope", "ref_id"]))
    return db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == scope, CircuitBreaker.ref_id == ref_id)
                     .with_for_update().execution_options(populate_existing=True))


def allows(db: Session, scope: str, ref_id: int) -> bool:
    """False while open. After the cool-down the circuit becomes half-open and lets one trial through."""
    cb = db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == scope, CircuitBreaker.ref_id == ref_id)
                   .execution_options(populate_existing=True))
    if cb is None or cb.state == "closed":
        return True
    if cb.state == "half_open":
        return False  # a trial is already in flight
    if cb.opened_at and datetime.now(UTC) - cb.opened_at >= timedelta(seconds=get_settings().circuit_cooldown_seconds):
        cb.state = "half_open"
        log_activity(db, f"circuit.{scope}_half_open", category="runtime", level="warning",
                     organization_id=cb.organization_id, details={"scope": scope, "id": ref_id})
        return True
    return False


def record_failure(db: Session, scope: str, ref_id: int, organization_id: int, reason: str) -> str:
    cb = _row(db, scope, ref_id, organization_id)
    cb.consecutive_failures += 1
    cb.last_failure = reason[:1000]
    if cb.state == "half_open" or (cb.state == "closed" and
                                   cb.consecutive_failures >= get_settings().circuit_failure_threshold):
        cb.state, cb.opened_at = "open", datetime.now(UTC)
        log_activity(db, f"circuit.{scope}_opened", category="runtime", level="error", organization_id=organization_id,
                     details={"scope": scope, "id": ref_id, "failures": cb.consecutive_failures, "reason": reason})
    return cb.state


def record_success(db: Session, scope: str, ref_id: int, organization_id: int) -> None:
    cb = db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == scope, CircuitBreaker.ref_id == ref_id))
    if cb is None or (cb.state == "closed" and cb.consecutive_failures == 0):
        return
    was = cb.state
    cb.state, cb.consecutive_failures, cb.opened_at = "closed", 0, None
    if was != "closed":
        log_activity(db, f"circuit.{scope}_closed", category="runtime", organization_id=organization_id,
                     details={"scope": scope, "id": ref_id})


def state(db: Session, scope: str, ref_id: int) -> str:
    cb = db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == scope, CircuitBreaker.ref_id == ref_id))
    return cb.state if cb else "closed"


def available(db: Session, scope: str, ref_id: int) -> bool:
    """Read-only variant of `allows` for ranking candidates (does not consume the half-open trial)."""
    cb = db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == scope, CircuitBreaker.ref_id == ref_id))
    if cb is None or cb.state == "closed":
        return True
    if cb.state == "open" and cb.opened_at:
        return datetime.now(UTC) - cb.opened_at >= timedelta(seconds=get_settings().circuit_cooldown_seconds)
    return False
