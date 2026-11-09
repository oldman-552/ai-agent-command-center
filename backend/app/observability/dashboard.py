"""Dashboard aggregates (spec T2). Every number comes from the database."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import Date, cast, func, select

from app.models import (
    Agent,
    AgentState,
    Approval,
    ApprovalStatus,
    AuditLog,
    CostRecord,
    Project,
    Repository,
    Task,
    TaskStatus,
    UsageRecord,
    User,
)
from app.security.auth import DB, require

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(db: DB, user: User = Depends(require("dashboard.read"))) -> dict:
    org = user.organization_id
    live_agents = select(Agent).where(Agent.organization_id == org, Agent.archived_at.is_(None)).subquery()

    def agent_count(*conds) -> int:
        return db.scalar(select(func.count()).select_from(live_agents).where(*conds)) or 0

    agents = {
        "total": agent_count(),
        "active": agent_count(live_agents.c.is_enabled.is_(True),
                              live_agents.c.state.in_([AgentState.RUNNING.value, AgentState.ASSIGNED.value,
                                                       AgentState.REVIEWING.value])),
        "available": agent_count(live_agents.c.is_enabled.is_(True), live_agents.c.state == AgentState.IDLE.value),
        "disabled": agent_count(live_agents.c.is_enabled.is_(False)),
        "failed": agent_count(live_agents.c.state == AgentState.FAILED.value),
    }

    task_rows = dict(db.execute(select(Task.status, func.count()).where(Task.organization_id == org)
                                 .group_by(Task.status)).all())
    tasks = {s.value: int(task_rows.get(s.value, 0)) for s in TaskStatus}
    finished = tasks["COMPLETED"] + tasks["FAILED"]
    success_rate = round(100.0 * tasks["COMPLETED"] / finished, 1) if finished else None
    pending_approvals = db.scalar(
        select(func.count()).select_from(Approval)
        .where(Approval.organization_id == org, Approval.status == ApprovalStatus.PENDING.value)
    ) or 0

    real = (UsageRecord.organization_id == org, UsageRecord.is_mock.is_(False))
    tokens_total = db.scalar(select(func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0))
                             .where(*real)) or 0
    cost_total = db.scalar(select(func.coalesce(func.sum(CostRecord.amount_usd), 0)).join(UsageRecord)
                           .where(*real)) or 0

    since = datetime.now(UTC).date() - timedelta(days=13)
    day = cast(UsageRecord.ts, Date)
    daily = {
        d: (int(t), float(c or 0))
        for d, t, c in db.execute(
            select(day, func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), func.sum(CostRecord.amount_usd))
            .outerjoin(CostRecord, CostRecord.usage_record_id == UsageRecord.id)
            .where(*real, day >= since)
            .group_by(day)
        ).all()
    }
    usage_series = [
        {"date": (since + timedelta(days=i)).isoformat(),
         "tokens": daily.get(since + timedelta(days=i), (0, 0.0))[0],
         "cost_usd": daily.get(since + timedelta(days=i), (0, 0.0))[1]}
        for i in range(14)
    ]

    recent = db.scalars(select(AuditLog).where(AuditLog.organization_id == org).order_by(AuditLog.id.desc())
                     .limit(10)).all()

    return {
        "agents": agents,
        "tasks": tasks,
        "tasks_awaiting_approval": pending_approvals,
        "task_success_rate": success_rate,
        "projects": db.scalar(select(func.count()).select_from(Project)
                              .where(Project.organization_id == org, Project.archived_at.is_(None))) or 0,
        "repositories": db.scalar(select(func.count()).select_from(Repository)
                                  .where(Repository.organization_id == org)) or 0,
        "tokens_total": int(tokens_total),
        "cost_usd_total": float(cost_total),
        "usage_series": usage_series,
        "recent_activity": [
            {"id": a.id, "ts": a.ts.isoformat(), "action": a.action, "category": a.category,
             "level": a.level, "message": a.message}
            for a in recent
        ],
    }
