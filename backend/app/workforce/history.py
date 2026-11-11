"""Execution history of an objective (WF-18): every id needed to follow one prompt through the platform.

    objective (workflow id, thread id) → Manager steps → tasks → workers → attempts (executions) → provider requests

Everything here is read from rows written while the work happened. Usage is shown only when a provider reported
it: a bridge reports none, and that is shown as "not reported", never as zero.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    Approval,
    Execution,
    ExecutionEvent,
    ManagerStep,
    Task,
    TaskSubmission,
    TaskTransition,
    UsageRecord,
    Workflow,
    WorkforceReport,
)
from app.workforce import service
from app.workforce.lifecycle import transition_out


def step_out(s: ManagerStep) -> dict[str, Any]:
    usage = (s.summary or {}).get("usage")
    return {"id": s.id, "kind": s.kind, "status": s.status, "attempt": s.attempt, "task_id": s.task_id,
            "submission_id": s.submission_id, "agent_id": s.agent_id, "provider": s.provider, "model": s.model,
            "is_mock": s.is_mock, "started_at": s.started_at, "ended_at": s.ended_at, "duration_ms": s.duration_ms,
            "error": s.error, "trace_id": s.trace_id, "provider_request_id": s.provider_request_id,
            "usage": None if not usage or usage.get("unknown") else usage,
            "usage_reported": bool(usage) and not usage.get("unknown")}


def execution_out(db: Session, e: Execution) -> dict[str, Any]:
    errors = [{"seq": ev.seq, "at": ev.ts, "code": ev.payload.get("code"), "category": ev.payload.get("category"),
               "delivered": ev.payload.get("delivered"), "retry_after": ev.payload.get("retry_after"),
               "request_id": ev.payload.get("request_id")}
              for ev in db.scalars(select(ExecutionEvent).where(ExecutionEvent.execution_id == e.id,
                                                                ExecutionEvent.type == "provider_error")
                                   .order_by(ExecutionEvent.seq))]
    retries = [{"seq": ev.seq, "at": ev.ts, "attempt": ev.payload.get("attempt"),
                "after_seconds": ev.payload.get("after_seconds")}
               for ev in db.scalars(select(ExecutionEvent).where(ExecutionEvent.execution_id == e.id,
                                                                 ExecutionEvent.type == "retry")
                                    .order_by(ExecutionEvent.seq))]
    usage_rows = db.scalars(select(UsageRecord).where(UsageRecord.execution_id == e.id)).all()
    reported = e.input_tokens is not None
    return {"id": e.id, "status": e.status, "agent_id": e.agent_id, "engine": e.engine, "provider": e.provider,
            "model": e.model, "is_mock": e.is_mock, "attempts": e.attempt, "retries": retries,
            "idempotency_key": e.idempotency_key, "provider_request_id": e.provider_request_id,
            "provider_request_ids": [u.provider_request_id for u in usage_rows if u.provider_request_id],
            "trace_ids": [x.get("trace_id") for x in (e.evidence or []) if x.get("kind") == "trace"],
            "created_at": e.created_at, "started_at": e.started_at, "ended_at": e.ended_at,
            # the runtime keeps the text of the last failed try on the row; on a run that then succeeded it is what
            # the run recovered from, not its outcome
            "error_category": e.error_category if e.status != "COMPLETED" else None,
            "error": e.error if e.status != "COMPLETED" else None,
            "recovered_from": e.error if e.status == "COMPLETED" and e.error else None, "provider_errors": errors,
            "usage_reported": reported,
            "usage": ({"input_tokens": e.input_tokens, "output_tokens": e.output_tokens,
                       "cached_tokens": e.cached_tokens,
                       "cost_usd": str(e.cost_usd) if e.cost_usd is not None else None} if reported else None)}


def submission_summary(s: TaskSubmission) -> dict[str, Any]:
    r = s.review or {}
    return {"id": s.id, "version": s.version, "attempt": s.attempt, "mode": s.mode, "source": s.source,
            "submitted_by": s.submitted_by, "execution_id": s.execution_id, "worker_id": s.worker_id,
            "sha256": s.content_sha256, "chars": len(s.content), "attachments": len(s.attachments or []),
            "is_mock": s.is_mock, "created_at": s.created_at, "decision": s.decision, "decided_by": s.decided_by,
            "decided_at": s.decided_at, "outcome": r.get("outcome"), "needs_human": bool(r.get("needs_human")),
            "manager_step_id": (r.get("manager") or {}).get("step_id"),
            "provider_request_id": (s.provider or {}).get("request_id")}


def timeline(db: Session, wf: Workflow) -> dict[str, Any]:
    steps = list(db.scalars(select(ManagerStep).where(ManagerStep.workflow_id == wf.id)
                            .order_by(ManagerStep.started_at, ManagerStep.id)))
    tasks = []
    for t in db.scalars(select(Task).where(Task.workflow_id == wf.id).order_by(Task.id)):
        w = service.worker_for_agent(db, t.assigned_agent_id)
        agent = db.get(Agent, t.assigned_agent_id) if t.assigned_agent_id else None
        tasks.append({
            "id": t.id, "title": t.title, "lifecycle": t.lifecycle, "status": t.status, "attempts": t.attempts,
            "worker": ({"id": w.id, "label": f"W{w.slot}", "agent_id": w.agent_id, "name": agent.name if agent else None}
                       if w else None),
            "mode": t.execution_mode, "assignment_source": t.assignment_source, "started_at": t.started_at,
            "finished_at": t.finished_at,
            "transitions": [transition_out(x) for x in db.scalars(
                select(TaskTransition).where(TaskTransition.task_id == t.id)
                .order_by(TaskTransition.ts, TaskTransition.id))],
            "executions": [execution_out(db, e) for e in db.scalars(
                select(Execution).where(Execution.task_id == t.id).order_by(Execution.created_at))],
            "submissions": [submission_summary(s) for s in db.scalars(
                select(TaskSubmission).where(TaskSubmission.task_id == t.id).order_by(TaskSubmission.version))],
            "approvals": [{"id": a.id, "operation": a.operation, "status": a.status, "approver_id": a.approver_id,
                           "decided_at": a.decided_at} for a in db.scalars(
                select(Approval).where(Approval.task_id == t.id).order_by(Approval.id))],
            "failures": (t.result or {}).get("failures", [])})
    reports = [{"id": r.id, "version": r.version, "status": r.status, "is_mock": r.is_mock, "created_at": r.created_at,
                "manager_step_id": r.manager_step_id}
               for r in db.scalars(select(WorkforceReport).where(WorkforceReport.workflow_id == wf.id)
                                   .order_by(WorkforceReport.version))]
    return {"objective_id": wf.id, "thread_id": wf.thread_id, "status": wf.status, "created_at": wf.created_at,
            "updated_at": wf.updated_at, "manager_steps": [step_out(s) for s in steps], "tasks": tasks,
            "reports": reports,
            "usage_note": "usage is listed only where a provider reported it; Arena Local Bridge reports none"}
