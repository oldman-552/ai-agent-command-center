"""Objectives of the workforce: submit, tick, cancel — and people moving tasks between workers (WF-06, WF-10).

`tick()` is the one step the worker process runs for an objective, and it is safe to run at any time:

    review what arrived  →  schedule (dependencies, assignment, dispatch)  →  consolidate when nothing is left

Every part re-reads its state from PostgreSQL and is idempotent, so a crash between two parts costs nothing.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bus.contract import MessageType, workflow_command
from app.bus.outbox import enqueue
from app.db import session_factory
from app.models import Agent, Execution, ExecutionMode, Project, Task, User, Workflow, Workforce, WorkforceWorker
from app.models import TaskLifecycle as L
from app.runtime import service as runtime
from app.security.audit import log_activity
from app.security.crypto import redact
from app.workforce import report, review, scheduler, service
from app.workforce.lifecycle import SETTLED, move
from app.workforce.scheduler import KIND, request_tick

log = logging.getLogger("workforce.control")
REASSIGNABLE = {L.PLANNED.value, L.READY.value, L.BLOCKED.value, L.ASSIGNED.value, L.WAITING_FOR_ACCEPTANCE.value,
                L.ACCEPTED.value, L.REVISION_REQUESTED.value}


class ControlError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def submit_objective(db: Session, force: Workforce, user: User, *, prompt: str, title: str | None = None,
                     project_id: int | None = None, review_policy: str | None = None,
                     idempotency_key: str | None = None) -> Workflow:
    """One prompt for the Manager. Planning happens in the worker process. Does not commit."""
    m = service.manager_out(db, force)
    if not m["configured"]:
        raise ControlError(m["problem"] or "manager_not_configured")
    project = db.get(Project, project_id or force.project_id) if (project_id or force.project_id) else None
    if project is None or project.organization_id != force.organization_id or project.archived_at is not None:
        raise ControlError("invalid_project_id", 422)
    key = f"workforce:{force.organization_id}:{idempotency_key}" if idempotency_key else None
    if key:  # the same submission twice is the same objective, never a second plan
        existing = db.scalar(select(Workflow).where(Workflow.thread_id == key))
        if existing is not None:
            return existing
    # Like every input of the platform: what looks like a secret is removed before the text is stored or sent to any
    # model or website. The person is told (`prompt_redacted`).
    typed = prompt.strip()
    text = redact(typed)
    shown_title = redact(title.strip()) if title and title.strip() else text.splitlines()[0]
    wf = Workflow(organization_id=force.organization_id, project_id=project.id, kind=KIND, workforce_id=force.id,
                  title=shown_title[:255], objective=text, status="planning",
                  created_by_user_id=user.id, review_policy=review_policy or force.review_policy,
                  thread_id=key or f"workforce:{uuid.uuid4()}",
                  plan={"prompt_redacted": True} if text != typed else {})
    db.add(wf)
    db.flush()
    enqueue(db, workflow_command(MessageType.WORKFLOW_PLAN, workflow_id=wf.id, organization_id=wf.organization_id,
                                 project_id=wf.project_id, sender=f"user:{user.id}", reason="submitted",
                                 idempotency_key=f"workflow.plan:{wf.id}"))
    log_activity(db, "workforce.objective_submitted", category="workforce", organization_id=wf.organization_id,
                 user_id=user.id, project_id=project.id,
                 details={"workflow_id": wf.id, "review_policy": wf.review_policy, "manager_is_mock": m["is_mock"],
                          "prompt_redacted": text != typed})
    return wf


async def plan(workflow_id: int) -> str:
    """Run (or continue) the Manager's analysis and planning for an objective. Returns planned | failed | skipped."""
    from app.workforce import manager
    with session_factory()() as db:
        wf = db.get(Workflow, workflow_id)
        if wf is None or wf.kind != KIND or wf.status != "planning":
            return "skipped"
        thread_id = wf.thread_id
    outcome = await manager.run_planning(workflow_id, thread_id)
    if outcome == "planned":
        with session_factory()() as db:
            request_tick(db, db.get(Workflow, workflow_id), "planned")
            db.commit()
    return outcome


def fail_planning(workflow_id: int, error: str) -> None:
    with session_factory()() as db:
        wf = db.get(Workflow, workflow_id, with_for_update=True)
        if wf is None or wf.kind != KIND or wf.status != "planning":
            return
        wf.status, wf.error = "failed", error[:2000]
        log_activity(db, "workforce.planning_failed", category="workforce", level="warning",
                     organization_id=wf.organization_id, project_id=wf.project_id, actor="manager",
                     details={"workflow_id": wf.id, "error": wf.error})
        db.commit()


async def tick(workflow_id: int) -> dict[str, Any]:
    """Review, schedule, consolidate. Idempotent."""
    with session_factory()() as db:
        wf = db.get(Workflow, workflow_id)
        if wf is None or wf.kind != KIND:
            return {"skipped": "not_workforce"}
        if wf.status == "planning":
            return {"skipped": "planning"}
        reviewed = {}
        if wf.status == "running":
            for sub_id in scheduler.pending_reviews(db, workflow_id):
                reviewed[sub_id] = await review.review_submission(db, sub_id)
        summary = scheduler.advance(db, workflow_id)
        db.expire_all()
        wf = db.get(Workflow, workflow_id)
        rep = None
        if wf.status in report.FINISHED_OBJECTIVE:
            rep = await report.consolidate(db, workflow_id)
        return {**summary, "reviewed": reviewed, "status": wf.status, "report_version": rep.version if rep else None}


def cancel_objective(db: Session, wf: Workflow, user: User, reason: str = "") -> Workflow:
    """Stop an objective: running executions are asked to stop, open tasks are cancelled. Commits."""
    wf = db.get(Workflow, wf.id, with_for_update=True, populate_existing=True)
    if wf.status in ("completed", "cancelled"):
        return wf
    actor = f"user:{user.id}"
    for t in db.scalars(select(Task).where(Task.workflow_id == wf.id, Task.lifecycle.not_in(sorted(SETTLED)))):
        for ex in db.scalars(select(Execution).where(Execution.task_id == t.id)):
            runtime.request_cancel(db, ex, actor)
        move(db, t, L.CANCELLED, actor=actor, reason=f"objective cancelled: {reason}"[:500])
    wf.status = "cancelled"
    log_activity(db, "workforce.objective_cancelled", category="workforce", level="warning",
                 organization_id=wf.organization_id, user_id=user.id, project_id=wf.project_id,
                 details={"workflow_id": wf.id, "reason": reason[:500]})
    request_tick(db, wf, "cancelled")  # consolidates what exists
    db.commit()
    return wf


def cancel_task(db: Session, task: Task, user: User, reason: str) -> Task:
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    if wf is None or wf.kind != KIND:
        raise ControlError("not_a_workforce_task")
    if task.lifecycle in SETTLED:
        raise ControlError(f"task_already_settled:{task.lifecycle}")
    actor = f"user:{user.id}"
    for ex in db.scalars(select(Execution).where(Execution.task_id == task.id)):
        runtime.request_cancel(db, ex, actor)
    move(db, task, L.CANCELLED, actor=actor, reason=f"cancelled: {reason}"[:500])
    log_activity(db, "workforce.task_cancelled", category="workforce", level="warning",
                 organization_id=task.organization_id, project_id=task.project_id, task_id=task.id, user_id=user.id,
                 details={"reason": reason[:500]})
    request_tick(db, wf, f"task {task.id} cancelled")
    db.commit()
    return task


def reassign(db: Session, task: Task, user: User, worker: WorkforceWorker, note: str) -> Task:
    """A person gives the task to another worker. Audited; nothing in flight is interrupted. Commits."""
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    if wf is None or wf.kind != KIND:
        raise ControlError("not_a_workforce_task")
    if worker.organization_id != task.organization_id or worker.workforce_id != wf.workforce_id:
        raise ControlError("invalid_worker_id", 422)
    if task.lifecycle not in REASSIGNABLE:
        # QUEUED/RUNNING: an automatic request is in flight — cancel it first, so no result arrives for nobody
        raise ControlError(f"task_not_reassignable:{task.lifecycle}")
    agent = db.get(Agent, worker.agent_id)
    if worker.mode == ExecutionMode.DISABLED.value or not agent.is_enabled or agent.archived_at is not None:
        raise ControlError("worker_disabled", 422)
    if task.assigned_agent_id == agent.id and task.lifecycle not in (L.READY.value, L.REVISION_REQUESTED.value):
        raise ControlError("already_assigned_to_this_worker")
    previous = task.assigned_agent_id
    old_worker = service.worker_for_agent(db, previous)
    actor = f"user:{user.id}"
    task.assigned_agent_id, task.assignment_source = agent.id, "manual"
    task.accepted_by_user_id = task.accepted_at = None
    result = dict(task.result or {})
    result.pop("declined_by_agents", None)
    result.pop("waiting", None)
    result["reassignments"] = [*result.get("reassignments", []), {
        "by": actor, "from_agent_id": previous, "to_agent_id": agent.id, "note": note[:500],
        "at": datetime.now(UTC).isoformat()}][-20:]
    task.result = result
    details = {"from_worker": f"W{old_worker.slot}" if old_worker else None, "to_worker": f"W{worker.slot}",
               "from_agent_id": previous, "to_agent_id": agent.id, "note": note[:500]}
    if task.lifecycle in (L.ASSIGNED.value, L.WAITING_FOR_ACCEPTANCE.value, L.ACCEPTED.value):
        move(db, task, L.READY, actor=actor, reason=f"reassigned to W{worker.slot}: {note}"[:500], details=details)
    else:
        from app.models import TaskTransition  # same state, different worker: still a history row
        db.add(TaskTransition(organization_id=task.organization_id, task_id=task.id, from_state=task.lifecycle,
                              to_state=task.lifecycle, actor=actor,
                              reason=f"reassigned to W{worker.slot}: {note}"[:500], details=details))
    log_activity(db, "workforce.task_reassigned", category="workforce", level="warning",
                 organization_id=task.organization_id, project_id=task.project_id, task_id=task.id, user_id=user.id,
                 agent_id=agent.id, details=details)
    scheduler.reopen_if_needed(db, wf)
    request_tick(db, wf, f"task {task.id} reassigned")
    db.commit()
    return task
