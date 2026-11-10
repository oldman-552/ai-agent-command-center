"""Agent Supervisor (M7, spec Phase 5, ADR-023) — independent of the orchestrator.

    sweep():  detectors → findings (one open finding per problem, deduplicated by fingerprint)
              → policy-authorized reversible actions → audit "supervisor.*"

The supervisor observes everything but can only do what is listed in ALLOWED_ACTIONS and enabled in settings:
stop a runaway execution, resume a stalled one from its checkpoint, isolate an unhealthy agent (its circuit), retry an
eligible failed task, reduce an agent's concurrency. It never decides approvals, changes permissions, merges, or
touches a task waiting for approval (SV-05) — those code paths are simply not reachable from here.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Agent,
    AuditLog,
    CircuitBreaker,
    Execution,
    ExecutionEvent,
    SupervisorFinding,
    Task,
    TaskDependency,
    Workflow,
    Worktree,
)
from app.models import ExecutionStatus as X
from app.security.audit import log_activity

log = logging.getLogger("supervisor")
ALLOWED_ACTIONS = {"stop_runaway", "resume_stalled", "isolate_agent", "retry_task", "reduce_concurrency"}
ACTIVE = [X.RUNNING.value, X.WAITING_FOR_TOOL.value, X.INITIALIZING.value, X.READY.value, X.RECOVERING.value]
NEVER_TOUCH_TASK = {"APPROVAL", "REVIEW", "COMPLETED", "CANCELLED"}  # human decisions pending or final


def _now() -> datetime:
    return datetime.now(UTC)


Finding = dict[str, Any]


# ---------------- detectors (SV-02, SV-04) ----------------
def detect(db: Session, org: int) -> list[Finding]:
    s = get_settings()
    now = _now()
    out: list[Finding] = []

    # stalled executions: active but silent
    last_event = (select(ExecutionEvent.execution_id, func.max(ExecutionEvent.ts).label("last"))
                  .group_by(ExecutionEvent.execution_id).subquery())
    for ex, last in db.execute(select(Execution, last_event.c.last).join(
            last_event, last_event.c.execution_id == Execution.id).where(
            Execution.organization_id == org, Execution.status.in_(ACTIVE),
            last_event.c.last < now - timedelta(seconds=s.supervisor_stall_seconds))).all():
        out.append({"kind": "stalled_execution", "severity": "warning", "subject_type": "execution",
                    "subject_id": ex.id, "details": {"status": ex.status, "silent_seconds": int((now - last).total_seconds()),
                                                     "agent_id": ex.agent_id, "task_id": ex.task_id},
                    "recommendation": "Resume from the last checkpoint on a healthy worker.", "action": "resume_stalled"})

    # runaway cost / token budget
    for ex, budget in db.execute(select(Execution, Agent.token_budget).join(Agent, Agent.id == Execution.agent_id).where(
            Execution.organization_id == org, Execution.status.in_(ACTIVE))).all():
        tokens = (ex.input_tokens or 0) + (ex.output_tokens or 0)
        over_cost = ex.cost_usd is not None and float(ex.cost_usd) > s.supervisor_runaway_cost_usd
        over_tokens = bool(budget) and tokens > budget
        if over_cost or over_tokens:
            out.append({"kind": "runaway_execution", "severity": "critical", "subject_type": "execution",
                        "subject_id": ex.id, "details": {"cost_usd": str(ex.cost_usd), "tokens": tokens,
                                                         "token_budget": budget, "agent_id": ex.agent_id},
                        "recommendation": "Stop it; it can be resumed after review.", "action": "stop_runaway"})

    # excessive retries
    for ex in db.scalars(select(Execution).where(Execution.organization_id == org, Execution.status.in_(ACTIVE),
                                                 Execution.attempt >= s.supervisor_retry_threshold)):
        out.append({"kind": "excessive_retries", "severity": "warning", "subject_type": "execution", "subject_id": ex.id,
                    "details": {"attempt": ex.attempt, "agent_id": ex.agent_id},
                    "recommendation": "Inspect the failure category before allowing more attempts.", "action": None})

    # repeated failures per agent
    since = now - timedelta(minutes=s.supervisor_failure_window_minutes)
    for agent_id, n in db.execute(select(Execution.agent_id, func.count()).where(
            Execution.organization_id == org, Execution.status == X.FAILED.value, Execution.ended_at >= since,
            Execution.error_category != "policy").group_by(Execution.agent_id)).all():
        if agent_id and n >= s.supervisor_repeated_failures:
            out.append({"kind": "repeated_failures", "severity": "critical", "subject_type": "agent",
                        "subject_id": str(agent_id), "details": {"failures": n, "window_minutes":
                                                                 s.supervisor_failure_window_minutes},
                        "recommendation": "Isolate the agent and reassign its work; check its model and provider.",
                        "action": "isolate_agent"})

    # suspicious tool usage: repeated policy denials
    for agent_id, n in db.execute(select(AuditLog.agent_id, func.count()).where(
            AuditLog.organization_id == org, AuditLog.ts >= since,
            AuditLog.action.in_(["runtime.tool_denied", "sandbox.command_denied", "devplane.protected_branch_blocked"]))
            .group_by(AuditLog.agent_id)).all():
        if agent_id and n >= s.supervisor_denials_threshold:
            out.append({"kind": "suspicious_tool_usage", "severity": "critical", "subject_type": "agent",
                        "subject_id": str(agent_id), "details": {"denials": n},
                        "recommendation": "Review the agent's instructions and inputs for prompt injection.",
                        "action": "reduce_concurrency"})

    # idle agents while compatible work waits
    waiting = db.scalars(select(Task).where(Task.organization_id == org, Task.status == "READY")).all()
    if waiting:
        idle = db.scalars(select(Agent).where(Agent.organization_id == org, Agent.is_enabled.is_(True),
                                              Agent.archived_at.is_(None), Agent.state == "IDLE")).all()
        for a in idle:
            caps = {a.role.lower(), *[str(x).lower() for x in a.skills or []]}
            fits = [t.id for t in waiting if {x.lower() for x in t.required_skills or []} <= caps]
            if fits:
                out.append({"kind": "idle_agent_with_waiting_work", "severity": "info", "subject_type": "agent",
                            "subject_id": str(a.id), "details": {"ready_tasks": fits[:20]},
                            "recommendation": "Check the project rosters; the orchestrator may not see this agent.",
                            "action": None})

    # workflow deadlock: running, nothing can move, but tasks remain
    for wf in db.scalars(select(Workflow).where(Workflow.organization_id == org, Workflow.status == "running")):
        statuses = [st for (st,) in db.execute(select(Task.status).where(Task.workflow_id == wf.id)).all()]
        moving = {"READY", "IN_PROGRESS", "REVIEW", "APPROVAL"}
        if statuses and not (set(statuses) & moving) and "PLANNED" in statuses:
            plan_ids = [tid for (tid,) in db.execute(select(Task.id).where(Task.workflow_id == wf.id,
                                                                          Task.status == "PLANNED")).all()]
            deps = db.execute(select(TaskDependency.task_id, TaskDependency.depends_on_id).where(
                TaskDependency.task_id.in_(plan_ids))).all()
            out.append({"kind": "deadlock", "severity": "critical", "subject_type": "workflow", "subject_id": str(wf.id),
                        "details": {"planned": plan_ids, "dependencies": [list(d) for d in deps][:50]},
                        "recommendation": "Planned tasks wait on tasks that will never finish; re-plan or cancel.",
                        "action": None})

    # duplicate work: same normalized title, both active, same project
    act = db.scalars(select(Task).where(Task.organization_id == org,
                                        Task.status.in_(["READY", "IN_PROGRESS", "PLANNED"]))).all()
    seen: dict[tuple, int] = {}
    for t in act:
        key = (t.project_id, re.sub(r"\W+", " ", t.title.lower().replace("[mock]", "")).strip())
        if key in seen:
            out.append({"kind": "duplicate_work", "severity": "warning", "subject_type": "task", "subject_id": str(t.id),
                        "details": {"duplicate_of": seen[key], "title": t.title},
                        "recommendation": "Cancel one of the two tasks.", "action": None})
        else:
            seen[key] = t.id

    # conflicting changes between agent worktrees
    for wt in db.scalars(select(Worktree).where(Worktree.organization_id == org,
                                                Worktree.status.in_(["active", "committed"]))):
        if wt.conflicts:
            out.append({"kind": "conflicting_changes", "severity": "warning", "subject_type": "worktree",
                        "subject_id": str(wt.id), "details": {"task_id": wt.task_id, "conflicts": wt.conflicts},
                        "recommendation": "Sequence the tasks or have one agent rebase on the other's work.",
                        "action": None})
    return out


# ---------------- actions (SV-03) ----------------
def _enabled() -> set[str]:
    return {a.strip() for a in get_settings().supervisor_actions.split(",") if a.strip()} & ALLOWED_ACTIONS


def act(db: Session, f: SupervisorFinding, action: str) -> dict[str, Any] | None:
    from app.runtime import circuit, service
    from app.runtime.dispatch import dispatch

    if action not in _enabled():
        return None
    if f.subject_type == "execution":
        ex = db.get(Execution, f.subject_id)
        if ex is None or ex.status not in ACTIVE:
            return None
        task = db.get(Task, ex.task_id) if ex.task_id else None
        if task is not None and task.status in NEVER_TOUCH_TASK:
            return None
        if action == "stop_runaway":
            service.request_cancel(db, ex, "supervisor")
            return {"action": "stop_runaway", "execution_id": ex.id, "reversible": "resume the execution"}
        if action == "resume_stalled":
            # The bus handler holds the execution's lock: if the owner is alive the message is deferred, if it died
            # the execution is recovered from its checkpoint. Either way nothing runs twice.
            dispatch(db, ex, "supervisor", f"resume:supervisor:{f.id}:{int(_now().timestamp())}")
            return {"action": "resume_stalled", "execution_id": ex.id, "reversible": "cancel the execution"}
    if f.subject_type == "agent":
        agent = db.get(Agent, int(f.subject_id))
        if agent is None:
            return None
        if action == "isolate_agent":
            cb = circuit._row(db, "agent", agent.id, agent.organization_id)
            if cb.state == "open":
                return None
            cb.state, cb.opened_at, cb.last_failure = "open", _now(), "isolated by supervisor"
            return {"action": "isolate_agent", "agent_id": agent.id,
                    "reversible": "POST /api/supervisor/agents/{id}/release or wait for the circuit cool-down"}
        if action == "reduce_concurrency" and agent.concurrency_limit > 1:
            before = agent.concurrency_limit
            agent.concurrency_limit = max(1, before // 2)
            return {"action": "reduce_concurrency", "agent_id": agent.id, "from": before, "to": agent.concurrency_limit,
                    "reversible": "raise the agent's concurrency limit"}
    if f.subject_type == "task" and action == "retry_task":
        task = db.get(Task, int(f.subject_id))
        if task is None or task.status != "FAILED" or task.attempts >= task.max_attempts:
            return None
        task.status, task.error = "READY", None
        return {"action": "retry_task", "task_id": task.id, "reversible": "cancel the task"}
    return None


def retry_candidates(db: Session, org: int) -> list[Finding]:
    """Failed tasks whose last failure was transient and which still have attempts left."""
    out = []
    for t in db.scalars(select(Task).where(Task.organization_id == org, Task.status == "FAILED",
                                           Task.attempts < Task.max_attempts)):
        last = db.scalar(select(Execution).where(Execution.task_id == t.id).order_by(Execution.created_at.desc()).limit(1))
        if last is not None and last.error_category in ("transient", "timeout"):
            out.append({"kind": "retryable_task_failure", "severity": "info", "subject_type": "task",
                        "subject_id": str(t.id), "details": {"category": last.error_category, "attempts": t.attempts},
                        "recommendation": "Retry: the failure was transient.", "action": "retry_task"})
    return out


# ---------------- sweep ----------------
def sweep(db: Session, organization_id: int) -> dict[str, Any]:
    found = [*detect(db, organization_id), *retry_candidates(db, organization_id)]
    seen_fp, actions = set(), []
    now = _now()
    for item in found:
        fp = f"{item['kind']}:{item['subject_type']}:{item['subject_id']}"
        seen_fp.add(fp)
        f = db.scalar(select(SupervisorFinding).where(SupervisorFinding.fingerprint == fp))
        if f is None:
            f = SupervisorFinding(organization_id=organization_id, kind=item["kind"], severity=item["severity"],
                                  subject_type=item["subject_type"], subject_id=str(item["subject_id"]), fingerprint=fp,
                                  details=item["details"], recommendation=item["recommendation"])
            db.add(f)
            db.flush()
            log_activity(db, f"supervisor.detected_{item['kind']}", category="supervisor",
                         level="error" if item["severity"] == "critical" else "warning",
                         organization_id=organization_id, actor="supervisor",
                         details={"finding_id": f.id, "subject": f"{item['subject_type']}:{item['subject_id']}"})
        else:
            f.details, f.last_seen_at = item["details"], now
            if f.status == "resolved":
                f.status, f.resolved_at, f.action = "open", None, None
        if item.get("action") and f.action is None:
            done = act(db, f, item["action"])
            if done is not None:
                f.action, f.status = {**done, "at": now.isoformat()}, "actioned"
                actions.append(done)
                log_activity(db, f"supervisor.{done['action']}", category="supervisor", level="warning",
                             organization_id=organization_id, actor="supervisor",
                             details={"finding_id": f.id, **done})
    # findings whose problem is gone resolve themselves
    for f in db.scalars(select(SupervisorFinding).where(SupervisorFinding.organization_id == organization_id,
                                                        SupervisorFinding.status.in_(["open", "actioned"]))):
        if f.fingerprint not in seen_fp:
            f.status, f.resolved_at = "resolved", now
    db.commit()
    if any(a["action"] == "retry_task" for a in actions):
        _nudge_workflows(db, [int(a["task_id"]) for a in actions if a["action"] == "retry_task"])
    return {"findings": len(found), "actions": actions}


def _nudge_workflows(db: Session, task_ids: list[int]) -> None:
    from app.orchestration.scheduler import request_advance
    for wf in {db.get(Task, tid).workflow_id for tid in task_ids} - {None}:
        w = db.get(Workflow, wf)
        if w and w.status == "running":
            request_advance(db, w, "supervisor retried a task")
    db.commit()


def release_agent(db: Session, agent: Agent, user_id: int) -> None:
    cb = db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == "agent", CircuitBreaker.ref_id == agent.id))
    if cb is not None:
        cb.state, cb.consecutive_failures, cb.opened_at = "closed", 0, None
    log_activity(db, "supervisor.agent_released", category="supervisor", organization_id=agent.organization_id,
                 user_id=user_id, agent_id=agent.id)
