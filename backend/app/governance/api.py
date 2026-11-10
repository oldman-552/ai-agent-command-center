"""Approval Center API (SEC-03). Reading needs approvals.read; deciding is HUMAN_ONLY (approvals.decide)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select

from app.governance import approvals as svc
from app.models import Approval, User
from app.security.auth import DB, require
from app.security.crypto import redact_obj
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def approval_out(a: Approval) -> dict:
    return {"id": a.id, "operation": a.operation, "operation_label": svc.OPERATIONS.get(a.operation, svc.Operation(
                a.operation, a.risk_level)).label,
            "risk_level": a.risk_level, "status": a.status, "title": a.title, "summary": a.summary,
            "payload": redact_obj(a.payload), "evidence": a.evidence, "agent_id": a.agent_id,
            "project_id": a.project_id, "task_id": a.task_id, "execution_id": a.execution_id,
            "requested_by": a.requested_by, "approver_id": a.approver_id, "decided_at": a.decided_at,
            "reason": a.reason, "executed_at": a.executed_at, "created_at": a.created_at,
            "executable": a.operation in svc._EXECUTORS}


@router.get("")
def list_approvals(db: DB, user: User = Depends(require("approvals.read")),
                   status_: str | None = Query(default=None, alias="status"), risk: str | None = None,
                   limit: int = Query(default=100, le=500)) -> dict:
    if svc.expire_stale(db, user.organization_id):
        db.commit()
    base = select(Approval).where(Approval.organization_id == user.organization_id)
    stmt = base
    if status_:
        stmt = stmt.where(Approval.status == status_)
    if risk:
        stmt = stmt.where(Approval.risk_level == risk)
    pending_first = case((Approval.status == "pending", 0), else_=1)
    rows = db.scalars(stmt.order_by(pending_first, Approval.id.desc()).limit(limit))
    counts = dict(db.execute(select(Approval.status, func.count()).where(
        Approval.organization_id == user.organization_id).group_by(Approval.status)).all())
    return {"items": [approval_out(a) for a in rows], "counts": counts}


@router.get("/{approval_id}")
def get_approval(approval_id: int, db: DB, user: User = Depends(require("approvals.read"))) -> dict:
    return approval_out(get_scoped(db, Approval, approval_id, user))


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject", "changes_requested"]
    reason: str = Field(default="", max_length=4000)


@router.post("/{approval_id}/decide")
def decide(approval_id: int, body: DecisionIn, db: DB, user: User = Depends(require("approvals.decide"))) -> dict:
    a = get_scoped(db, Approval, approval_id, user)
    a = svc.decide(db, a, user_id=user.id, decision=body.decision, reason=body.reason)
    db.commit()
    return approval_out(a)
