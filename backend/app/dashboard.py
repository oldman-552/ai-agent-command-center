"""Dashboard aggregates (spec T2). Every number comes from the database."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import Date, cast, func, select

from app.auth import DB, CurrentUser
from app.models import (
    ActivityLog,
    Agent,
    AgentRuntimeStatus,
    ApprovalRequest,
    ApprovalStatus,
    GitHubRepository,
    Project,
    Task,
    TaskStatus,
    UsageStatistic,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(_: CurrentUser, db: DB) -> dict:
    live_agents = select(Agent).where(Agent.is_archived.is_(False)).subquery()

    def agent_count(*conds) -> int:
        return db.scalar(select(func.count()).select_from(live_agents).where(*conds)) or 0

    agents = {
        "total": agent_count(),
        "active": agent_count(live_agents.c.is_enabled.is_(True),
                              live_agents.c.runtime_status == AgentRuntimeStatus.RUNNING.value),
        "available": agent_count(live_agents.c.is_enabled.is_(True),
                                 live_agents.c.runtime_status == AgentRuntimeStatus.IDLE.value),
        "disabled": agent_count(live_agents.c.is_enabled.is_(False)),
        "failed": agent_count(live_agents.c.runtime_status == AgentRuntimeStatus.FAILED.value),
    }

    task_rows = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
    tasks = {s.value: int(task_rows.get(s.value, 0)) for s in TaskStatus}
    finished = tasks["completed"] + tasks["failed"]
    success_rate = round(100.0 * tasks["completed"] / finished, 1) if finished else None
    pending_approvals = db.scalar(
        select(func.count()).select_from(ApprovalRequest)
        .where(ApprovalRequest.status == ApprovalStatus.PENDING.value)
    ) or 0

    usage = db.execute(select(
        func.coalesce(func.sum(UsageStatistic.input_tokens + UsageStatistic.output_tokens), 0),
        func.coalesce(func.sum(UsageStatistic.cost_usd), 0),
    ).where(UsageStatistic.is_mock.is_(False))).one()

    since = datetime.now(UTC).date() - timedelta(days=13)
    day = cast(UsageStatistic.ts, Date)
    daily = {
        d: (int(t), float(c))
        for d, t, c in db.execute(
            select(day, func.sum(UsageStatistic.input_tokens + UsageStatistic.output_tokens),
                   func.sum(UsageStatistic.cost_usd))
            .where(UsageStatistic.is_mock.is_(False), day >= since)
            .group_by(day)
        ).all()
    }
    usage_series = [
        {"date": (since + timedelta(days=i)).isoformat(),
         "tokens": daily.get(since + timedelta(days=i), (0, 0.0))[0],
         "cost_usd": daily.get(since + timedelta(days=i), (0, 0.0))[1]}
        for i in range(14)
    ]

    recent = db.scalars(select(ActivityLog).order_by(ActivityLog.id.desc()).limit(10)).all()

    return {
        "agents": agents,
        "tasks": tasks,
        "tasks_awaiting_approval": pending_approvals,
        "task_success_rate": success_rate,
        "projects": db.scalar(select(func.count()).select_from(Project)
                              .where(Project.is_archived.is_(False))) or 0,
        "repositories": db.scalar(select(func.count()).select_from(GitHubRepository)) or 0,
        "tokens_total": int(usage[0]),
        "cost_usd_total": float(usage[1]),
        "usage_series": usage_series,
        "recent_activity": [
            {"id": a.id, "ts": a.ts.isoformat(), "action": a.action, "category": a.category,
             "level": a.level, "message": a.message}
            for a in recent
        ],
    }
