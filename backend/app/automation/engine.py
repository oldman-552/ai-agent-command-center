"""In-app automation & scheduling (UX-06, spec Phase 24, ADR-028).

Automations live in the database and are run by the worker's `automations` role (one leader at a time). Nothing is
ever installed into the operating system (no cron entries, no Windows scheduled tasks, no services).

Triggers
  {"type": "cron", "expr": "0 3 * * 1-5"}          5-field cron in UTC (croniter)
  {"type": "interval", "minutes": 60}               every N minutes (≥ 5)
  {"type": "event", "action": "github.pr_merged"}   whenever an audit event with that action is recorded in the
                                                    automation's project (or organization when no project)
Actions
  {"type": "workflow", "title": "...", "objective": "..."}   submit an objective to the orchestrator
  {"type": "analyze_project"}                                re-run the existing-project repository analysis
Every run is recorded (`automation_runs`) with its outcome; paused automations never run; the creator's permissions
are re-checked at run time (a disabled or demoted creator stops the automation).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from croniter import croniter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, Automation, AutomationRun, Project, User, Workflow
from app.security.audit import log_activity

MIN_INTERVAL_MINUTES = 5
ACTION_PERMISSION = {"workflow": "workflows.run", "analyze_project": "projects.write"}
EVENT_BATCH = 50


class AutomationError(ValueError):
    pass


def validate(trigger: dict[str, Any], action: dict[str, Any], project_id: int | None) -> None:
    t = trigger.get("type")
    if t == "cron":
        expr = str(trigger.get("expr", ""))
        if len(expr.split()) != 5 or not croniter.is_valid(expr):
            raise AutomationError("invalid cron expression (5 fields, UTC)")
        it = croniter(expr, datetime.now(UTC))
        a, b = it.get_next(datetime), it.get_next(datetime)
        if (b - a) < timedelta(minutes=MIN_INTERVAL_MINUTES):
            raise AutomationError(f"runs more often than every {MIN_INTERVAL_MINUTES} minutes")
    elif t == "interval":
        if int(trigger.get("minutes", 0)) < MIN_INTERVAL_MINUTES:
            raise AutomationError(f"interval must be at least {MIN_INTERVAL_MINUTES} minutes")
    elif t == "event":
        if not str(trigger.get("action", "")).strip() or len(str(trigger["action"])) > 64:
            raise AutomationError("event trigger needs an audit action name")
        if str(trigger["action"]).startswith("automation."):
            raise AutomationError("automations cannot trigger on automation events (loops)")
    else:
        raise AutomationError("trigger type must be cron, interval or event")
    a = action.get("type")
    if a not in ACTION_PERMISSION:
        raise AutomationError("action type must be workflow or analyze_project")
    if project_id is None:
        raise AutomationError("automations belong to a project")
    if a == "workflow" and not (str(action.get("objective", "")).strip() and str(action.get("title", "")).strip()):
        raise AutomationError("workflow action needs a title and an objective")


def next_run(trigger: dict[str, Any], after: datetime) -> datetime | None:
    if trigger["type"] == "cron":
        return croniter(trigger["expr"], after).get_next(datetime)
    if trigger["type"] == "interval":
        return after + timedelta(minutes=int(trigger["minutes"]))
    return None  # event-driven


def _creator_allowed(db: Session, a: Automation) -> str | None:
    u = db.get(User, a.created_by_user_id) if a.created_by_user_id else None
    if u is None or not u.is_active:
        return "creator account is missing or disabled"
    if ACTION_PERMISSION[a.action["type"]] not in {p.key for p in u.role.permissions}:
        return f"creator no longer holds {ACTION_PERMISSION[a.action['type']]}"
    return None


def _do(db: Session, a: Automation, run: AutomationRun) -> dict[str, Any]:
    act = a.action
    if act["type"] == "workflow":
        from app.bus.contract import MessageType, workflow_command
        from app.bus.outbox import enqueue
        wf = Workflow(organization_id=a.organization_id, project_id=a.project_id, title=str(act["title"])[:255],
                      objective=str(act["objective"])[:20_000], status="planning",
                      created_by_user_id=a.created_by_user_id,
                      thread_id=f"workflow:automation:{a.id}:{run.id}:{uuid.uuid4().hex[:8]}")
        db.add(wf)
        db.flush()
        enqueue(db, workflow_command(MessageType.WORKFLOW_PLAN, workflow_id=wf.id, organization_id=wf.organization_id,
                                     project_id=wf.project_id, sender=f"automation:{a.id}", reason="automation",
                                     idempotency_key=f"workflow.plan:{wf.id}"))
        return {"workflow_id": wf.id}
    from app.devplane.worktrees import repo_path
    from app.orchestration.inspector import analyze
    p = db.get(Project, a.project_id)
    path = repo_path(p.organization_id, p.id)
    if not path.is_dir() or not any(path.iterdir()):
        raise AutomationError("the project has no repository to analyze")
    p.analysis, p.analyzed_at = analyze(path), datetime.now(UTC)
    return {"files": p.analysis["files"]}


def run_once(db: Session, a: Automation, trigger: str, detail: str = "") -> AutomationRun:
    """Run one automation now and record it. Commits nothing; the caller commits."""
    run = AutomationRun(automation_id=a.id, organization_id=a.organization_id, trigger=trigger, status="running",
                        detail=detail[:2000])
    db.add(run)
    db.flush()
    blocked = _creator_allowed(db, a)
    if blocked:
        run.status, run.detail = "skipped", blocked
    else:
        try:
            with db.begin_nested():
                run.result = _do(db, a, run)
            run.status = "succeeded"
        except Exception as e:  # noqa: BLE001 — recorded as the run's outcome
            run.status, run.detail = "failed", f"{type(e).__name__}: {e}"[:2000]
    run.finished_at = datetime.now(UTC)
    a.last_run_at = run.finished_at
    log_activity(db, f"automation.run_{run.status}", category="automation", organization_id=a.organization_id,
                 project_id=a.project_id, actor=f"automation:{a.id}",
                 level="warning" if run.status != "succeeded" else "info",
                 details={"automation_id": a.id, "run_id": run.id, "trigger": trigger, "detail": run.detail[:300]})
    return run


def tick(db: Session, now: datetime | None = None) -> list[int]:
    """Run everything that is due. Returns run ids. The caller commits."""
    now = now or datetime.now(UTC)
    runs = []
    rows = db.scalars(select(Automation).where(Automation.status == "active").with_for_update(skip_locked=True)).all()
    for a in rows:
        t = a.trigger.get("type")
        if t in ("cron", "interval"):
            if a.next_run_at is None:
                a.next_run_at = next_run(a.trigger, now)
                continue
            if a.next_run_at <= now:
                runs.append(run_once(db, a, "schedule").id)
                a.next_run_at = next_run(a.trigger, now)
        elif t == "event":
            cursor = int((a.state or {}).get("cursor", 0))
            if cursor == 0:  # start listening from now, never replay history
                last = db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or 0
                a.state = {**(a.state or {}), "cursor": last}
                continue
            top = db.scalar(select(AuditLog.id).order_by(AuditLog.id.desc()).limit(1)) or cursor
            stmt = select(AuditLog).where(AuditLog.id > cursor, AuditLog.id <= top,
                                          AuditLog.organization_id == a.organization_id,
                                          AuditLog.action == a.trigger["action"])
            if a.project_id is not None:
                stmt = stmt.where(AuditLog.project_id == a.project_id)
            events = db.scalars(stmt.order_by(AuditLog.id).limit(EVENT_BATCH)).all()
            for ev in events:
                runs.append(run_once(db, a, "event", f"audit #{ev.id} {ev.action}").id)
            a.state = {**(a.state or {}), "cursor": events[-1].id if len(events) == EVENT_BATCH else top}
    return runs
