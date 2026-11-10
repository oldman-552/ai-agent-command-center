"""Agent performance profiles (EV-02, spec Phase 14 "Agent Performance Profiles").

Profiles are computed on demand from `evaluation_results` (the evaluator's independent verdicts), never from an
agent's own claims. They are grouped by agent, role, provider, model, task type or project, and the per-agent,
per-task-type profile feeds task assignment (OR-02) as the "quality" factor.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Agent, EvaluationResult, Task

GROUPS = ("agent", "role", "provider", "model", "task_type", "project")
WINDOW = 50       # most recent evaluations per agent considered for assignment
MIN_SAMPLES = 3   # below this the profile is shown but not trusted for assignment


def _group_col(group: str):
    return {"agent": EvaluationResult.agent_id, "role": Agent.role, "provider": EvaluationResult.provider,
            "model": EvaluationResult.model, "task_type": EvaluationResult.task_type,
            "project": Task.project_id}[group]


def profiles(db: Session, organization_id: int, group: str, task_type: str | None = None) -> list[dict[str, Any]]:
    if group not in GROUPS:
        raise ValueError(group)
    key = _group_col(group)
    approved = func.sum(case((EvaluationResult.decision == "approve", 1), else_=0))
    stmt = (select(key, func.count(), approved, func.avg(EvaluationResult.overall),
                   func.avg(EvaluationResult.scores["task_completion"].as_float()),
                   func.avg(EvaluationResult.scores["test_pass_rate"].as_float()),
                   func.max(EvaluationResult.created_at))
            .select_from(EvaluationResult)
            .outerjoin(Agent, Agent.id == EvaluationResult.agent_id)
            .outerjoin(Task, Task.id == EvaluationResult.task_id)
            .where(EvaluationResult.organization_id == organization_id))
    if task_type:
        stmt = stmt.where(EvaluationResult.task_type == task_type)
    rows = db.execute(stmt.group_by(key).order_by(func.count().desc())).all()
    out = []
    for k, n, ok, overall, completion, tests, last in rows:
        out.append({"key": k, "evaluations": n, "approve_rate": round(ok / n, 3) if n else None,
                    "avg_overall": round(float(overall), 3) if overall is not None else None,
                    "avg_task_completion": round(float(completion), 3) if completion is not None else None,
                    "avg_test_pass_rate": round(float(tests), 3) if tests is not None else None,
                    "last_evaluated_at": last, "trusted": n >= MIN_SAMPLES})
    return out


def agent_quality(db: Session, agent_ids: list[int], task_type: str | None) -> dict[int, dict[str, float]]:
    """Recent approve rate per agent for this task type (falls back to all task types when too few samples)."""
    def window(tt: str | None):
        ranked = (select(EvaluationResult.agent_id, EvaluationResult.decision,
                         func.row_number().over(partition_by=EvaluationResult.agent_id,
                                                order_by=EvaluationResult.created_at.desc()).label("rn"))
                  .where(EvaluationResult.agent_id.in_(agent_ids)))
        if tt:
            ranked = ranked.where(EvaluationResult.task_type == tt)
        sub = ranked.subquery()
        rows = db.execute(select(sub.c.agent_id, func.count(),
                                 func.sum(case((sub.c.decision == "approve", 1), else_=0)))
                          .where(sub.c.rn <= WINDOW).group_by(sub.c.agent_id)).all()
        return {aid: {"n": n, "quality": ok / n} for aid, n, ok in rows if n >= MIN_SAMPLES}

    specific = window(task_type) if task_type else {}
    general = window(None)
    return {aid: specific.get(aid) or general[aid] for aid in set(specific) | set(general)}
