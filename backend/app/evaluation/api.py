"""Evaluation results and performance profiles API (EV-02, EV-05). Read-only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.evaluation import profiles as prof
from app.models import Agent, EvaluationResult, Task, User
from app.security.auth import DB, require

router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


@router.get("")
def recent(db: DB, user: User = Depends(require("tasks.read")), decision: str | None = None,
           task_type: str | None = None, agent_id: int | None = None,
           limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    stmt = (select(EvaluationResult, Task.title, Agent.name).outerjoin(Task, Task.id == EvaluationResult.task_id)
            .outerjoin(Agent, Agent.id == EvaluationResult.agent_id)
            .where(EvaluationResult.organization_id == user.organization_id))
    if decision:
        stmt = stmt.where(EvaluationResult.decision == decision)
    if task_type:
        stmt = stmt.where(EvaluationResult.task_type == task_type)
    if agent_id:
        stmt = stmt.where(EvaluationResult.agent_id == agent_id)
    rows = db.execute(stmt.order_by(EvaluationResult.id.desc()).limit(limit)).all()
    return [{"id": e.id, "task_id": e.task_id, "task_title": title, "agent_id": e.agent_id, "agent_name": name,
             "execution_id": e.execution_id, "provider": e.provider, "model": e.model, "task_type": e.task_type,
             "decision": e.decision, "overall": e.overall, "scores": e.scores, "evidence": e.evidence,
             "created_at": e.created_at} for e, title, name in rows]


@router.get("/profiles")
def profiles(db: DB, user: User = Depends(require("tasks.read")), group: str = "agent",
             task_type: str | None = None) -> dict:
    if group not in prof.GROUPS:
        raise HTTPException(422, "invalid_group")
    rows = prof.profiles(db, user.organization_id, group, task_type)
    if group == "agent":
        names = dict(db.execute(select(Agent.id, Agent.name).where(
            Agent.id.in_([r["key"] for r in rows if r["key"] is not None]))).all())
        for r in rows:
            r["label"] = names.get(r["key"])
    return {"group": group, "min_samples": prof.MIN_SAMPLES, "items": rows}
