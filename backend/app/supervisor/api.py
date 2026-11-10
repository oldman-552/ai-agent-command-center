"""Supervisor API (SV-06): health, findings, manual sweep, release an isolated agent."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.models import Agent, AuditLog, CircuitBreaker, Execution, SupervisorFinding, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped
from app.supervisor import engine

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])


def finding_out(f: SupervisorFinding) -> dict:
    return {"id": f.id, "kind": f.kind, "severity": f.severity, "subject_type": f.subject_type,
            "subject_id": f.subject_id, "details": f.details, "recommendation": f.recommendation, "action": f.action,
            "status": f.status, "detected_at": f.detected_at, "last_seen_at": f.last_seen_at,
            "resolved_at": f.resolved_at}


@router.get("/health")
def health(db: DB, user: User = Depends(require("agents.read"))) -> dict:
    org = user.organization_id
    agents = dict(db.execute(select(Agent.state, func.count()).where(
        Agent.organization_id == org, Agent.archived_at.is_(None)).group_by(Agent.state)).all())
    executions = dict(db.execute(select(Execution.status, func.count()).where(
        Execution.organization_id == org, Execution.status.in_(engine.ACTIVE)).group_by(Execution.status)).all())
    findings = dict(db.execute(select(SupervisorFinding.kind, func.count()).where(
        SupervisorFinding.organization_id == org, SupervisorFinding.status != "resolved")
        .group_by(SupervisorFinding.kind)).all())
    isolated = db.scalar(select(func.count()).select_from(CircuitBreaker).where(
        CircuitBreaker.organization_id == org, CircuitBreaker.scope == "agent", CircuitBreaker.state != "closed"))
    last = db.scalar(select(func.max(AuditLog.ts)).where(AuditLog.organization_id == org,
                                                         AuditLog.action == "supervisor.sweep"))
    return {"agents": agents, "active_executions": executions, "open_findings": findings,
            "isolated_agents": isolated, "last_sweep_at": last,
            "autonomous_actions": sorted(engine._enabled())}


@router.get("/findings")
def findings(db: DB, user: User = Depends(require("agents.read")), status: str | None = None,
             limit: int = Query(default=100, le=500)) -> list[dict]:
    stmt = select(SupervisorFinding).where(SupervisorFinding.organization_id == user.organization_id)
    if status == "open":
        stmt = stmt.where(SupervisorFinding.status != "resolved")
    elif status:
        stmt = stmt.where(SupervisorFinding.status == status)
    return [finding_out(f) for f in db.scalars(stmt.order_by(SupervisorFinding.last_seen_at.desc()).limit(limit))]


@router.get("/actions")
def actions(db: DB, user: User = Depends(require("agents.read")), limit: int = Query(default=50, le=200)) -> list[dict]:
    rows = db.scalars(select(AuditLog).where(AuditLog.organization_id == user.organization_id,
                                             AuditLog.category == "supervisor", AuditLog.action != "supervisor.sweep")
                      .order_by(AuditLog.id.desc()).limit(limit))
    return [{"id": e.id, "ts": e.ts, "action": e.action, "actor": e.actor, "details": e.details} for e in rows]


@router.post("/run")
def run(db: DB, user: User = Depends(require("agents.control"))) -> dict:
    result = engine.sweep(db, user.organization_id)
    log_activity(db, "supervisor.sweep", category="supervisor", organization_id=user.organization_id,
                 user_id=user.id, details={"findings": result["findings"], "actions": len(result["actions"]),
                                           "manual": True})
    db.commit()
    return result


@router.post("/findings/{finding_id}/resolve")
def resolve(finding_id: int, db: DB, user: User = Depends(require("agents.control"))) -> dict:
    f = get_scoped(db, SupervisorFinding, finding_id, user)
    f.status, f.resolved_at = "resolved", datetime.now(UTC)
    log_activity(db, "supervisor.finding_resolved", category="supervisor", organization_id=user.organization_id,
                 user_id=user.id, details={"finding_id": f.id})
    db.commit()
    return finding_out(f)


@router.post("/agents/{agent_id}/release")
def release(agent_id: int, db: DB, user: User = Depends(require("agents.control"))) -> dict:
    a = get_scoped(db, Agent, agent_id, user)
    engine.release_agent(db, a, user.id)
    db.commit()
    return {"agent_id": a.id, "circuit": "closed"}
