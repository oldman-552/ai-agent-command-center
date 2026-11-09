"""Task assignment intelligence (OR-02, spec Phase 4 "Task Assignment Intelligence").

Hard filters (never violated): same organization, enabled, not archived, not DISABLED/FAILED/BLOCKED, engine available,
member of the project when the project has an agent roster, has every required skill, has free concurrency.
Soft score among the eligible: role fit, historical success rate, current load, average cost and latency.
The decision and its full score breakdown are returned so the orchestrator can record *why* an agent was chosen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Agent, Execution, Project, Task
from app.models import ExecutionStatus as X
from app.models.agents import project_agents
from app.runtime.contract import TERMINAL, registered_engines

UNAVAILABLE_STATES = {"DISABLED", "FAILED", "BLOCKED"}
HISTORY_WINDOW = 50
WEIGHTS = {"role": 2.0, "success": 1.5, "load": 1.0, "cost": 0.5, "latency": 0.3}


@dataclass
class Candidate:
    agent: Agent
    score: float
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class Selection:
    agent: Agent | None
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)


def capabilities(agent: Agent) -> set[str]:
    return {agent.role.lower(), *[str(s).lower() for s in (agent.skills or [])]}


def _active(db: Session, agent_ids: list[int]) -> dict[int, int]:
    rows = db.execute(select(Execution.agent_id, func.count()).where(
        Execution.agent_id.in_(agent_ids), Execution.status.not_in([s.value for s in TERMINAL | {X.FAILED}]))
        .group_by(Execution.agent_id)).all()
    return dict(rows)


def _history(db: Session, agent_ids: list[int]) -> dict[int, dict[str, float]]:
    """Recent finished executions per agent: success rate, mean cost, mean duration (seconds)."""
    ranked = select(Execution.agent_id, Execution.status, Execution.cost_usd, Execution.started_at,
                    Execution.ended_at,
                    func.row_number().over(partition_by=Execution.agent_id,
                                           order_by=Execution.created_at.desc()).label("rn")
                    ).where(Execution.agent_id.in_(agent_ids),
                            Execution.status.in_([X.COMPLETED.value, X.FAILED.value])).subquery()
    dur = func.extract("epoch", ranked.c.ended_at - ranked.c.started_at)
    rows = db.execute(select(
        ranked.c.agent_id, func.count(),
        func.sum(case((ranked.c.status == X.COMPLETED.value, 1), else_=0)),
        func.avg(ranked.c.cost_usd), func.avg(dur)).where(ranked.c.rn <= HISTORY_WINDOW)
        .group_by(ranked.c.agent_id)).all()
    return {aid: {"n": n, "success": ok / n if n else 0.0, "cost": float(c or 0), "latency": float(d or 0)}
            for aid, n, ok, c, d in rows}


def select_agent(db: Session, task: Task) -> Selection:
    stmt = select(Agent).where(Agent.organization_id == task.organization_id, Agent.is_enabled.is_(True),
                               Agent.archived_at.is_(None), Agent.state.not_in(UNAVAILABLE_STATES),
                               Agent.execution_engine.in_(registered_engines()))
    if task.project_id:
        roster = db.scalars(select(project_agents.c.agent_id).where(project_agents.c.project_id == task.project_id)).all()
        project = db.get(Project, task.project_id)
        if roster and project:
            stmt = stmt.where(Agent.id.in_(roster))
    pool = list(db.scalars(stmt.order_by(Agent.id)))
    need = {s.lower() for s in (task.required_skills or [])}
    skilled = [a for a in pool if need <= capabilities(a)]
    if not skilled:
        return Selection(None, "no_agent_with_required_skills" if pool else "no_eligible_agent")
    ids = [a.id for a in skilled]
    active, hist = _active(db, ids), _history(db, ids)
    free = [a for a in skilled if active.get(a.id, 0) < a.concurrency_limit]
    if not free:
        return Selection(None, "all_capable_agents_busy")

    costs = [hist.get(a.id, {}).get("cost", 0.0) for a in free]
    lats = [hist.get(a.id, {}).get("latency", 0.0) for a in free]
    max_cost, max_lat = max(costs) or 1.0, max(lats) or 1.0
    scored: list[Candidate] = []
    for a in free:
        h = hist.get(a.id)
        b = {
            "role": 1.0 if task.task_type and task.task_type.lower() == a.role.lower() else 0.0,
            "success": h["success"] if h else 0.5,  # unknown history: neutral prior
            "load": 1.0 - active.get(a.id, 0) / max(a.concurrency_limit, 1),
            "cost": -(h["cost"] / max_cost) if h else 0.0,
            "latency": -(h["latency"] / max_lat) if h else 0.0,
        }
        scored.append(Candidate(a, sum(WEIGHTS[k] * v for k, v in b.items()), b))
    scored.sort(key=lambda c: (-c.score, c.agent.priority, c.agent.id))  # deterministic tie-break
    best = scored[0]
    return Selection(best.agent, "selected", [
        {"agent_id": c.agent.id, "score": round(c.score, 4), **{k: round(v, 4) for k, v in c.breakdown.items()}}
        for c in scored[:5]])
