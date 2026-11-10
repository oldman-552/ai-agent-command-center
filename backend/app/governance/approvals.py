"""Approval Center (SEC-03, spec Phase 16, ADR-017).

Critical operations requested by agents or by the platform are never executed directly. They become an approval
request whose *exact* payload is frozen and hashed at request time. Only a human with `approvals.decide` (a
HUMAN_ONLY permission) can decide, never on their own request. After approval the platform executes that same
payload exactly once; a payload changed after the request is refused. Pending requests never stop other work.

Humans acting directly with HUMAN_ONLY permissions (e.g. rotating a key on the Providers page) are themselves the
authorization; this center gates what *agents and automation* ask for.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bus.contract import Envelope, MessageType
from app.bus.outbox import enqueue
from app.models import Approval
from app.models import ApprovalStatus as A
from app.models import RiskLevel as R
from app.security.audit import log_activity
from app.security.crypto import redact_obj

log = logging.getLogger("governance.approvals")
PENDING_TTL = timedelta(days=7)
_RISK_ORDER = [R.LOW.value, R.MEDIUM.value, R.HIGH.value, R.CRITICAL.value]


@dataclass(frozen=True)
class Operation:
    label: str
    min_risk: str


# The catalogue of critical operations (spec Phase 16). A requester may raise the risk level, never lower it.
OPERATIONS: dict[str, Operation] = {
    "deploy.production": Operation("Production deployment", R.CRITICAL.value),
    "db.destructive": Operation("Destructive database operation", R.CRITICAL.value),
    "exposure.public": Operation("External public exposure", R.HIGH.value),
    "credential.change": Operation("Credential change", R.HIGH.value),
    "permission.escalation": Operation("Permission escalation", R.HIGH.value),
    "infra.sensitive": Operation("Sensitive infrastructure change", R.HIGH.value),
    "git.merge_protected": Operation("Merge into a protected branch", R.HIGH.value),
    "irreversible": Operation("Irreversible operation", R.HIGH.value),
    "task.critical": Operation("Accept a task that performs a critical operation", R.HIGH.value),
    "budget.increase": Operation("Additional spending beyond a budget", R.HIGH.value),
}

Executor = Callable[[Session, Approval], dict[str, Any]]
_EXECUTORS: dict[str, Executor] = {}


def register_executor(operation: str, fn: Executor) -> None:
    if operation not in OPERATIONS:
        raise ValueError(f"unknown critical operation {operation!r}")
    _EXECUTORS[operation] = fn


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _frozen_hash(a: Approval) -> str | None:
    return next((e.get("sha256") for e in a.evidence or [] if e.get("kind") == "payload"), None)


def _event(a: Approval, type_: MessageType) -> Envelope:
    return Envelope(message_type=type_, sender="approvals", recipient="*", organization_id=a.organization_id,
                    project_id=a.project_id, task_id=a.task_id, correlation_id=f"approval:{a.id}",
                    idempotency_key=f"{type_}:{a.id}:{a.status}",
                    payload={"approval_id": a.id, "operation": a.operation, "status": a.status,
                             "risk_level": a.risk_level})


def request_approval(db: Session, *, organization_id: int, operation: str, title: str, requested_by: str,
                     payload: dict[str, Any], summary: str = "", risk_level: str | None = None,
                     evidence: list[dict[str, Any]] | None = None, agent_id: int | None = None,
                     project_id: int | None = None, task_id: int | None = None, execution_id: str | None = None,
                     idempotency_key: str | None = None) -> Approval:
    """Create (or return the existing) pending request. Does not commit."""
    op = OPERATIONS.get(operation)
    if op is None:
        raise ValueError(f"unknown critical operation {operation!r}")
    risk = risk_level if risk_level in _RISK_ORDER else op.min_risk
    risk = max(risk, op.min_risk, key=_RISK_ORDER.index)
    if idempotency_key:
        existing = db.scalar(select(Approval).where(Approval.idempotency_key == idempotency_key))
        if existing is not None:
            if existing.organization_id != organization_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_conflict")
            return existing
    a = Approval(organization_id=organization_id, operation=operation, risk_level=risk, status=A.PENDING.value,
                 title=title[:255], summary=summary, payload=payload, requested_by=requested_by,
                 evidence=[{"kind": "payload", "sha256": payload_hash(payload),
                            "at": datetime.now(UTC).isoformat()}, *redact_obj(evidence or [])],
                 agent_id=agent_id, project_id=project_id, task_id=task_id, execution_id=execution_id,
                 idempotency_key=idempotency_key)
    db.add(a)
    db.flush()
    log_activity(db, "approvals.requested", category="approvals", level="warning", organization_id=organization_id,
                 actor=requested_by, agent_id=agent_id, project_id=project_id, task_id=task_id,
                 execution_id=execution_id, details={"approval_id": a.id, "operation": operation, "risk": risk})
    enqueue(db, _event(a, MessageType.APPROVAL_REQUESTED))
    return a


def expire_stale(db: Session, organization_id: int) -> int:
    cutoff = datetime.now(UTC) - PENDING_TTL
    rows = db.scalars(select(Approval).where(Approval.organization_id == organization_id,
                                             Approval.status == A.PENDING.value, Approval.created_at < cutoff)).all()
    for a in rows:
        a.status, a.reason, a.decided_at = A.EXPIRED.value, "expired without a decision", datetime.now(UTC)
        log_activity(db, "approvals.expired", category="approvals", organization_id=a.organization_id,
                     details={"approval_id": a.id})
    return len(rows)


def decide(db: Session, a: Approval, *, user_id: int, decision: str, reason: str) -> Approval:
    """approve | reject | changes_requested. Approve executes the frozen payload when an executor exists."""
    a = db.get(Approval, a.id, with_for_update=True, populate_existing=True)
    if a.status == A.PENDING.value and a.created_at < datetime.now(UTC) - PENDING_TTL:
        expire_stale(db, a.organization_id)
        db.commit()
    if a.status != A.PENDING.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"approval_not_pending:{a.status}")
    if a.requested_by == f"user:{user_id}":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "self_approval_forbidden")
    if decision not in ("approve", "reject", "changes_requested"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_decision")
    if decision != "approve" and not reason.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "reason_required")
    a.status = {"approve": A.APPROVED, "reject": A.REJECTED, "changes_requested": A.CHANGES_REQUESTED}[decision].value
    a.approver_id, a.decided_at, a.reason = user_id, datetime.now(UTC), reason[:4000] or None
    log_activity(db, f"approvals.{a.status}", category="approvals", level="warning",
                 organization_id=a.organization_id, user_id=user_id, project_id=a.project_id, task_id=a.task_id,
                 details={"approval_id": a.id, "operation": a.operation})
    enqueue(db, _event(a, MessageType.APPROVAL_DECIDED))
    if a.status == A.APPROVED.value and a.operation in _EXECUTORS:
        try:
            with db.begin_nested():  # an executor failure must not undo the recorded decision
                execute(db, a)
        except HTTPException as e:
            a.evidence = [*a.evidence, {"kind": "execution_failed", "error": str(e.detail),
                                        "at": datetime.now(UTC).isoformat()}]
            log_activity(db, "approvals.execution_failed", category="approvals", level="error",
                         organization_id=a.organization_id, details={"approval_id": a.id, "error": str(e.detail)})
    _notify(db, a)
    return a


def execute(db: Session, a: Approval) -> dict[str, Any]:
    """Run the approved operation exactly once with the payload that was approved."""
    if a.status == A.EXECUTED.value:
        return {"already_executed": True}
    if a.status != A.APPROVED.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"approval_not_approved:{a.status}")
    if payload_hash(a.payload) != _frozen_hash(a):
        log_activity(db, "approvals.tamper_detected", category="approvals", level="error",
                     organization_id=a.organization_id, details={"approval_id": a.id})
        raise HTTPException(status.HTTP_409_CONFLICT, "payload_changed_after_request")
    result = _EXECUTORS[a.operation](db, a)
    a.status, a.executed_at = A.EXECUTED.value, datetime.now(UTC)
    a.evidence = [*a.evidence, {"kind": "execution", "result": redact_obj(result), "at": a.executed_at.isoformat()}]
    log_activity(db, "approvals.executed", category="approvals", level="warning", organization_id=a.organization_id,
                 project_id=a.project_id, task_id=a.task_id, details={"approval_id": a.id, "operation": a.operation})
    return result


# Listeners for decisions that other subsystems care about (e.g. the orchestrator's approval stage).
_LISTENERS: list[Callable[[Session, Approval], None]] = []


def on_decision(fn: Callable[[Session, Approval], None]) -> None:
    _LISTENERS.append(fn)


def _notify(db: Session, a: Approval) -> None:
    for fn in _LISTENERS:
        fn(db, a)


# ---------------- built-in executors ----------------
def _escalate_agent_permissions(db: Session, a: Approval) -> dict[str, Any]:
    """payload: {agent_id, add_tools}. Applied only within the agent's security-profile ceiling."""
    from app.models import Agent
    from app.runtime.tools import disallowed_tools

    agent = db.get(Agent, int(a.payload["agent_id"]))
    if agent is None or agent.organization_id != a.organization_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "agent_missing")
    add = [str(t) for t in a.payload.get("add_tools", [])]
    bad = disallowed_tools(agent.security_profile, add)
    if bad:  # an approval can never lift the profile ceiling
        raise HTTPException(status.HTTP_409_CONFLICT, f"outside_security_profile:{','.join(bad)}")
    from app.control.agents import _snapshot
    from app.models import AgentConfigVersion

    agent.tool_permissions = sorted(set(agent.tool_permissions or []) | set(add))
    agent.config_version += 1
    db.add(AgentConfigVersion(agent_id=agent.id, version=agent.config_version, snapshot=_snapshot(agent),
                              changed_by_user_id=a.approver_id))
    return {"agent_id": agent.id, "tool_permissions": agent.tool_permissions}


register_executor("permission.escalation", _escalate_agent_permissions)
