"""AI Workforce API (`/api/workforce/*`): the workforce and its workers, objectives, the task board, Human-Assisted
work, reviews, reports and history. Every answer is built from persisted rows.

Permissions (server-side, see `app.security.rbac`):
    workforce.read    see everything here          workforce.review  review, reassign, reopen, cancel
    workforce.run     give an objective            workforce.manage  configure the Manager and the workers
    workforce.work    accept and submit task cards (bridges: `app.connectivity.arena_bridge.api`)
Rows of another organization are answered 404.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm.attributes import flag_modified

from app.config import get_settings
from app.connectivity.arena_bridge import service as bridge
from app.models import (
    Agent,
    Approval,
    AuditLog,
    ExecutionMode,
    ModelInfo,
    Provider,
    Task,
    TaskAttachment,
    TaskDependency,
    TaskSubmission,
    User,
    Workflow,
    WorkforceReport,
    WorkforceWorker,
    Worktree,
)
from app.models import TaskLifecycle as L
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped
from app.workforce import control, devtasks, history, human, lifecycle, report, review, scheduler, service
from app.workforce.scheduler import KIND

router = APIRouter(prefix="/api/workforce", tags=["workforce"])
OPEN_LIFECYCLES = [x.value for x in L if x.value not in lifecycle.SETTLED]


def _perms(user: User) -> set[str]:
    return {p.key for p in user.role.permissions}


def _force(db, user: User):
    wf = service.get(db, user.organization_id)
    if wf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workforce_not_set_up")
    return wf


def _objective(db, objective_id: int, user: User) -> Workflow:
    wf = get_scoped(db, Workflow, objective_id, user)
    if wf.kind != KIND:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return wf


def _task(db, task_id: int, user: User) -> Task:
    t = get_scoped(db, Task, task_id, user)
    if not scheduler.is_workforce_task(db, t):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return t


def _fail(e: Exception) -> HTTPException:
    code = getattr(e, "code", None) or str(e)
    return HTTPException(getattr(e, "status", status.HTTP_409_CONFLICT), code)


# ---------------- workforce, Manager, workers ----------------
@router.get("")
def get_workforce(db: DB, user: User = Depends(require("workforce.read"))) -> dict:
    wf = service.get(db, user.organization_id)
    return {"workforce": service.workforce_out(db, wf) if wf else None,
            "modes": [m.value for m in ExecutionMode], "default_mode": ExecutionMode.HUMAN_ASSISTED.value}


@router.post("/setup", status_code=201)
def setup(db: DB, user: User = Depends(require("workforce.manage"))) -> dict:
    """Create the default workforce (a Manager and four workers) when the organization has none."""
    wf = service.ensure_default(db, user.organization_id, actor=f"user:{user.id}")
    db.commit()
    return service.workforce_out(db, wf)


@router.get("/options")
def options(db: DB, user: User = Depends(require("workforce.manage"))) -> dict:
    """What can be chosen for the Manager and for a worker: providers with their models; for a bridge, its sessions
    with the (masked) account that owns each."""
    out = []
    for p in db.scalars(select(Provider).where(Provider.organization_id == user.organization_id)
                        .order_by(Provider.priority, Provider.id)):
        models = [{"name": m.name, "display_name": m.display_name}
                  for m in db.scalars(select(ModelInfo).where(ModelInfo.provider_id == p.id).order_by(ModelInfo.id))]
        entry: dict[str, Any] = {"id": p.id, "name": p.name, "kind": p.kind, "enabled": p.enabled,
                                 "is_mock": p.is_mock, "is_bridge": bridge.is_bridge(p), "models": models}
        if bridge.is_bridge(p):
            health = p.health or {}
            entry.update(terms_acknowledged=bridge.acknowledged(p), health_fresh=bridge.health_is_fresh(p),
                         usable=bridge.usable(health),
                         sessions=[{"id": s["session_id"], "title": s.get("title"), "model": s.get("model"),
                                    "account": s.get("account"), "account_hash": s.get("account_hash"),
                                    "state": s.get("state")} for s in health.get("sessions") or []],
                         pooled_models=["active", "agent"])
        out.append(entry)
    people = [{"id": u.id, "username": u.username} for u in db.scalars(
        select(User).where(User.organization_id == user.organization_id, User.is_active.is_(True)).order_by(User.id))
        if "workforce.work" in _perms(u)]
    return {"providers": out, "people": people}


class ManagerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_id: int | None = None
    model: str | None = Field(default=None, max_length=128)
    fallback_provider_id: int | None = None
    fallback_model: str | None = Field(default=None, max_length=128)
    instructions: str | None = Field(default=None, max_length=8000)
    timeout_seconds: int | None = Field(default=None, ge=30, le=3600)
    review_policy: Literal["manager", "human"] | None = None
    max_revisions: int | None = Field(default=None, ge=0, le=10)


@router.patch("/manager")
def update_manager(body: ManagerPatch, db: DB, user: User = Depends(require("workforce.manage"))) -> dict:
    wf = _force(db, user)
    try:
        service.update_manager(db, wf, user=user, patch=body.model_dump(exclude_unset=True))
    except service.ConfigError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    db.commit()
    return service.workforce_out(db, wf)


class DevelopmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    test_command: list[str] | None = Field(default=None, max_length=64)  # null or [] = no tests
    timeout_seconds: int | None = Field(default=None, ge=30, le=1800)


@router.patch("/development")
def update_development(body: DevelopmentPatch, db: DB, user: User = Depends(require("workforce.manage"))) -> dict:
    """WF-14: development tasks on or off, and the test command the platform runs in the sandbox. The command must
    pass the sandbox's `shell.test` allow-list."""
    wf = _force(db, user)
    try:
        dev = devtasks.update_settings(
            wf, enabled=body.enabled, timeout_seconds=body.timeout_seconds,
            test_command=body.test_command if "test_command" in body.model_fields_set else "keep")
    except devtasks.DevTaskError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    flag_modified(wf, "settings")
    log_activity(db, "workforce.development_updated", category="workforce", level="warning",
                 organization_id=user.organization_id, user_id=user.id, details=dev.out())
    db.commit()
    return service.workforce_out(db, wf)


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_attempts: int = Field(ge=1, le=10)
    backoff_seconds: float = Field(ge=0, le=600)
    backoff_multiplier: float = Field(ge=1, le=10)


class WorkerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    skills: list[str] | None = Field(default=None, max_length=30)
    prompt: str | None = Field(default=None, max_length=20_000)
    mode: ExecutionMode | None = None
    provider_id: int | None = None
    model: str | None = Field(default=None, max_length=128)
    timeout_seconds: int | None = Field(default=None, ge=30, le=7200)
    retry_policy: RetryPolicy | None = None
    concurrency_limit: int | None = Field(default=None, ge=1, le=8)
    account_label: str | None = Field(default=None, max_length=128)
    fallback_to_human: bool | None = None
    assignee_user_id: int | None = None
    browser_session_id: int | None = None  # ADR-035: drive this worker through its own visible browser window


@router.patch("/workers/{worker_id}")
def update_worker(worker_id: int, body: WorkerPatch, db: DB,
                  user: User = Depends(require("workforce.manage"))) -> dict:
    w = get_scoped(db, WorkforceWorker, worker_id, user)
    patch = body.model_dump(exclude_unset=True)
    if "mode" in patch and patch["mode"] is not None:
        patch["mode"] = patch["mode"].value if hasattr(patch["mode"], "value") else patch["mode"]
    if patch.get("skills") is not None:
        patch["skills"] = [s.strip().lower()[:64] for s in patch["skills"] if s.strip()]
    try:
        service.update_worker(db, w, user=user, patch=patch)
    except service.ConfigError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    for wf in db.scalars(select(Workflow).where(Workflow.organization_id == user.organization_id,
                                                Workflow.kind == KIND, Workflow.status == "running")):
        scheduler.request_tick(db, wf, f"worker {w.id} changed")  # waiting tasks may be able to move now
    db.commit()
    return service.worker_out(db, w)


class WorkerBrowserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: str = Field(default="arena", max_length=32)


@router.post("/workers/{worker_id}/browser")
async def open_worker_browser(worker_id: int, body: WorkerBrowserIn, db: DB,
                              user: User = Depends(require("workforce.manage"))) -> dict:
    """ADR-035: open (or reuse) this worker's own visible browser window — a profile of its own, signed in by hand —
    and make the worker drive it. Nothing is sent to the site; the window stays open for watching and stepping in."""
    from app.browser import api as browser_api
    from app.browser import client as browser_client
    from app.browser.bridge import ACTIVE, BridgeError
    from app.models import BrowserSession
    if "browser_bridge.manage" not in {p.key for p in user.role.permissions}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "browser_bridge.manage required")
    w = get_scoped(db, WorkforceWorker, worker_id, user)
    agent = db.get(Agent, w.agent_id)
    profile = browser_api.own_profile(db, user, agent)  # agent-<identifier>: one profile per worker
    s = db.scalar(select(BrowserSession).where(BrowserSession.organization_id == user.organization_id,
                                               BrowserSession.profile_name == profile,
                                               BrowserSession.state.in_(ACTIVE)))
    if s is not None and s.provider_key != body.site:
        raise HTTPException(status.HTTP_409_CONFLICT, "profile_in_use")
    if s is None:
        db.commit()
        snap = await browser_api.create_session(browser_api.SessionIn(provider=body.site, profile=profile,
                                                                      agent_id=agent.id), db, user)
        sid = int(snap["id"])
    else:
        sid = s.id
        if s.state == "AUTH_REQUIRED":  # the person may have signed in by hand meanwhile: look again
            try:
                await browser_client.call("POST", f"/sessions/{sid}/recheck")
            except BridgeError as e:
                if e.code != "not_waiting_for_auth":
                    raise browser_api._http(e) from e
    db.expire_all()
    w = get_scoped(db, WorkforceWorker, worker_id, user)
    try:
        service.update_worker(db, w, user=user, patch={"browser_session_id": sid})
    except service.ConfigError as e:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    for wf in db.scalars(select(Workflow).where(Workflow.organization_id == user.organization_id,
                                                Workflow.kind == KIND, Workflow.status == "running")):
        scheduler.request_tick(db, wf, f"worker {w.id} browser window")
    db.commit()
    return service.worker_out(db, w)


# ---------------- objectives ----------------
class ObjectiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=20_000)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    project_id: int | None = None
    review_policy: Literal["manager", "human"] | None = None
    idempotency_key: str | None = Field(default=None, max_length=96)


def _progress(db, wf_id: int) -> dict[str, Any]:
    rows = dict(db.execute(select(Task.lifecycle, func.count()).where(Task.workflow_id == wf_id)
                           .group_by(Task.lifecycle)).all())
    total = sum(rows.values())
    return {"total": total, "by_lifecycle": rows, "completed": rows.get(L.COMPLETED.value, 0),
            "settled": sum(n for s, n in rows.items() if s in lifecycle.SETTLED),
            "with_people": rows.get(L.WAITING_FOR_ACCEPTANCE.value, 0) + rows.get(L.ACCEPTED.value, 0),
            "under_review": rows.get(L.UNDER_REVIEW.value, 0) + rows.get(L.SUBMITTED.value, 0)}


def objective_out(db, wf: Workflow, *, full: bool = False) -> dict[str, Any]:
    plan = wf.plan or {}
    rep = report.latest(db, wf.id)
    rounds = plan.get("rounds") or []
    out = {"id": wf.id, "title": wf.title, "prompt": wf.objective, "status": wf.status, "error": wf.error,
           "review_policy": wf.review_policy, "project_id": wf.project_id, "created_by_user_id": wf.created_by_user_id,
           "created_at": wf.created_at, "updated_at": wf.updated_at, "progress": _progress(db, wf.id),
           "manager": plan.get("manager"), "plan_summary": plan.get("summary"),
           # something that looked like a secret was removed from the prompt before anything was stored or sent
           "prompt_redacted": bool(plan.get("prompt_redacted")),
           "report": ({"id": rep.id, "version": rep.version, "status": rep.status, "is_mock": rep.is_mock,
                       "counts": rep.content.get("counts"), "created_at": rep.created_at} if rep else None)}
    if full:
        tasks = list(db.scalars(select(Task).where(Task.workflow_id == wf.id).order_by(Task.id)))
        deps = db.scalars(select(TaskDependency).where(TaskDependency.task_id.in_([t.id for t in tasks]))).all() \
            if tasks else []
        out.update(analysis=plan.get("analysis"), waiting=(rounds[-1].get("waiting") if rounds else {}),
                   tasks=[card(db, t) for t in tasks],
                   dependencies=[{"task_id": d.task_id, "depends_on_id": d.depends_on_id} for d in deps])
    return out


@router.post("/objectives", status_code=201)
def submit_objective(body: ObjectiveIn, db: DB, user: User = Depends(require("workforce.run"))) -> dict:
    force = _force(db, user)
    try:
        wf = control.submit_objective(db, force, user, prompt=body.prompt, title=body.title,
                                      project_id=body.project_id, review_policy=body.review_policy,
                                      idempotency_key=body.idempotency_key)
    except control.ControlError as e:
        db.rollback()
        raise _fail(e) from e
    db.commit()
    return objective_out(db, wf)


@router.get("/objectives")
def list_objectives(db: DB, user: User = Depends(require("workforce.read")),
                    status_: str | None = Query(default=None, alias="status", max_length=32),
                    limit: int = Query(default=50, ge=1, le=200)) -> list[dict]:
    stmt = select(Workflow).where(Workflow.organization_id == user.organization_id, Workflow.kind == KIND)
    if status_:
        stmt = stmt.where(Workflow.status == status_)
    return [objective_out(db, w) for w in db.scalars(stmt.order_by(Workflow.id.desc()).limit(limit))]


@router.get("/objectives/{objective_id}")
def get_objective(objective_id: int, db: DB, user: User = Depends(require("workforce.read"))) -> dict:
    return objective_out(db, _objective(db, objective_id, user), full=True)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=500)


@router.post("/objectives/{objective_id}/cancel")
def cancel_objective(objective_id: int, body: ReasonIn, db: DB,
                     user: User = Depends(require("workforce.review"))) -> dict:
    wf = control.cancel_objective(db, _objective(db, objective_id, user), user, body.reason)
    return objective_out(db, wf)


@router.post("/objectives/{objective_id}/tick")
def nudge(objective_id: int, db: DB, user: User = Depends(require("workforce.review"))) -> dict:
    """Ask the platform to look at the objective again (after fixing a bridge, enabling a worker…)."""
    wf = _objective(db, objective_id, user)
    if wf.status not in ("running", "completed", "failed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"objective_not_schedulable:{wf.status}")
    scheduler.request_tick(db, wf, f"nudged by user:{user.id}")
    db.commit()
    return objective_out(db, wf)


def _report(db, wf: Workflow, version: int | None) -> WorkforceReport:
    stmt = select(WorkforceReport).where(WorkforceReport.workflow_id == wf.id)
    stmt = stmt.where(WorkforceReport.version == version) if version else stmt.order_by(
        WorkforceReport.version.desc()).limit(1)
    rep = db.scalar(stmt)
    if rep is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no_report_yet")
    return rep


@router.get("/objectives/{objective_id}/report")
def get_report(objective_id: int, db: DB, user: User = Depends(require("workforce.read")),
               version: int | None = Query(default=None, ge=1)) -> dict:
    wf = _objective(db, objective_id, user)
    rep = _report(db, wf, version)
    versions = [{"version": v, "status": s, "created_at": at} for v, s, at in db.execute(
        select(WorkforceReport.version, WorkforceReport.status, WorkforceReport.created_at)
        .where(WorkforceReport.workflow_id == wf.id).order_by(WorkforceReport.version))]
    return {**report.report_out(rep), "versions": versions}


@router.get("/objectives/{objective_id}/report.md", response_class=PlainTextResponse)
def get_report_markdown(objective_id: int, db: DB, user: User = Depends(require("workforce.read")),
                        version: int | None = Query(default=None, ge=1)) -> PlainTextResponse:
    wf = _objective(db, objective_id, user)
    rep = _report(db, wf, version)
    return PlainTextResponse(rep.markdown, media_type="text/markdown; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="workforce-report-{wf.id}-v{rep.version}.md"',
        "X-Content-Type-Options": "nosniff"})


@router.get("/objectives/{objective_id}/history")
def get_history(objective_id: int, db: DB, user: User = Depends(require("workforce.read"))) -> dict:
    return history.timeline(db, _objective(db, objective_id, user))


# ---------------- tasks ----------------
def _needs_human(sub: TaskSubmission | None) -> bool:
    return bool(sub is not None and sub.decision is None and (sub.review or {}).get("needs_human"))


def card(db, t: Task, user: User | None = None) -> dict[str, Any]:
    w = service.worker_for_agent(db, t.assigned_agent_id)
    agent = db.get(Agent, t.assigned_agent_id) if t.assigned_agent_id else None
    accepted_by = db.get(User, t.accepted_by_user_id) if t.accepted_by_user_id else None
    sub = scheduler.latest_submission(db, t.id) if t.lifecycle in (
        L.SUBMITTED.value, L.UNDER_REVIEW.value, L.APPROVED.value) else None
    result = t.result or {}
    out = {
        "id": t.id, "objective_id": t.workflow_id, "title": t.title, "lifecycle": t.lifecycle, "status": t.status,
        "priority": t.priority, "mode": t.execution_mode, "assignment_source": t.assignment_source,
        "worker": ({"id": w.id, "label": f"W{w.slot}", "slot": w.slot, "name": agent.name if agent else None,
                    "mode": w.mode, "assignee_user_id": w.assignee_user_id} if w else None),
        "attempts": t.attempts, "max_attempts": t.max_attempts, "required_skills": t.required_skills or [],
        "criteria": len(t.acceptance_criteria or []), "critical_operation": t.critical_operation,
        "kind": result.get("kind") or "text",
        "needs_human_review": _needs_human(sub),
        "review_reason": (sub.review or {}).get("reason") if _needs_human(sub) else None,
        "waiting": result.get("waiting") if t.lifecycle in (L.READY.value, L.REVISION_REQUESTED.value) else None,
        "fallback": result.get("fallback") if t.execution_mode == ExecutionMode.HUMAN_ASSISTED.value else None,
        "accepted_by": {"id": accepted_by.id, "username": accepted_by.username} if accepted_by else None,
        "approval_id": result.get("approval_id") if t.lifecycle == L.APPROVED.value else None,
        "error": t.error, "created_at": t.created_at, "updated_at": t.updated_at, "started_at": t.started_at,
        "finished_at": t.finished_at}
    if user is not None:
        perms = _perms(user)
        workable = human.can_work(t, w, user, perms)
        out["can"] = {
            "accept": t.lifecycle == L.WAITING_FOR_ACCEPTANCE.value and workable,
            "decline": t.lifecycle in (L.WAITING_FOR_ACCEPTANCE.value, L.ACCEPTED.value) and workable,
            "submit": t.lifecycle == L.ACCEPTED.value and t.accepted_by_user_id == user.id,
            "review": t.lifecycle in (L.SUBMITTED.value, L.UNDER_REVIEW.value) and "workforce.review" in perms,
            "reassign": t.lifecycle in control.REASSIGNABLE and "workforce.review" in perms,
            "reopen": t.lifecycle in (L.FAILED.value, L.REJECTED.value) and "workforce.review" in perms,
            "cancel": t.lifecycle not in lifecycle.SETTLED and "workforce.review" in perms}
    return out


def submission_out(db, s: TaskSubmission) -> dict[str, Any]:
    return {"id": s.id, "task_id": s.task_id, "version": s.version, "attempt": s.attempt, "mode": s.mode,
            "source": s.source, "submitted_by": s.submitted_by, "execution_id": s.execution_id,
            "worker_id": s.worker_id, "content": s.content, "sha256": s.content_sha256, "notes": s.notes,
            "attachments": s.attachments or [], "provider": s.provider or {}, "is_mock": s.is_mock,
            "review": s.review, "decision": s.decision, "decided_by": s.decided_by, "decided_at": s.decided_at,
            "created_at": s.created_at}


@router.get("/tasks")
def list_tasks(db: DB, user: User = Depends(require("workforce.read")), objective_id: int | None = None,
               lifecycle_: list[str] | None = Query(default=None, alias="lifecycle"),
               worker_id: int | None = None, mode: ExecutionMode | None = None,
               queue: Literal["mine", "offered", "review", "open"] | None = None,
               q: str | None = Query(default=None, max_length=100),
               limit: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> dict:
    """The task board. `queue`: `offered` = cards waiting for a person that I may take, `mine` = cards I accepted,
    `review` = results a person has to decide on, `open` = everything not settled."""
    stmt = (select(Task).join(Workflow, Workflow.id == Task.workflow_id)
            .where(Task.organization_id == user.organization_id, Workflow.kind == KIND))
    if objective_id:
        stmt = stmt.where(Task.workflow_id == objective_id)
    if lifecycle_:
        stmt = stmt.where(Task.lifecycle.in_(lifecycle_))
    if worker_id:
        w = get_scoped(db, WorkforceWorker, worker_id, user)
        stmt = stmt.where(Task.assigned_agent_id == w.agent_id)
    if mode:
        stmt = stmt.where(Task.execution_mode == mode.value)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Task.title.ilike(like), Task.instructions.ilike(like)))
    if queue == "mine":
        stmt = stmt.where(Task.lifecycle == L.ACCEPTED.value, Task.accepted_by_user_id == user.id)
    elif queue == "offered":
        stmt = stmt.where(Task.lifecycle == L.WAITING_FOR_ACCEPTANCE.value)
    elif queue == "review":
        stmt = stmt.where(Task.lifecycle.in_([L.UNDER_REVIEW.value, L.SUBMITTED.value]))
    elif queue == "open":
        stmt = stmt.where(Task.lifecycle.in_(OPEN_LIFECYCLES))
    rows = list(db.scalars(stmt.order_by(Task.priority.desc(), Task.id)))
    perms = _perms(user)
    if queue == "offered":
        rows = [t for t in rows if human.can_work(t, service.worker_for_agent(db, t.assigned_agent_id), user, perms)]
    if queue == "review":
        rows = [t for t in rows if _needs_human(scheduler.latest_submission(db, t.id))]
    page = rows[offset:offset + limit]
    return {"total": len(rows), "tasks": [card(db, t, user) for t in page],
            "lifecycles": [x.value for x in L]}


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: DB, user: User = Depends(require("workforce.read"))) -> dict:
    t = _task(db, task_id, user)
    dep_ids = list(db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == t.id)))
    dependents = list(db.scalars(select(TaskDependency.task_id).where(TaskDependency.depends_on_id == t.id)))
    related = {x.id: x for x in db.scalars(select(Task).where(Task.id.in_(dep_ids + dependents)))} if (
        dep_ids or dependents) else {}
    wf = db.get(Workflow, t.workflow_id)
    out = card(db, t, user)
    out.update(
        objective={"id": wf.id, "title": wf.title, "status": wf.status, "review_policy": wf.review_policy},
        task_objective=t.objective, instructions=t.instructions or t.description, expected_output=t.expected_output,
        acceptance_criteria=t.acceptance_criteria or [], timeout_seconds=t.timeout_seconds,
        depends_on=[{"id": i, "title": related[i].title, "lifecycle": related[i].lifecycle} for i in dep_ids],
        dependents=[{"id": i, "title": related[i].title, "lifecycle": related[i].lifecycle} for i in dependents],
        review_notes=(t.result or {}).get("review_notes", []), failures=(t.result or {}).get("failures", []),
        declines=(t.result or {}).get("declines", []), reassignments=(t.result or {}).get("reassignments", []),
        transitions=[lifecycle.transition_out(x) for x in lifecycle.history(db, t.id)],
        submissions=[submission_out(db, s) for s in db.scalars(
            select(TaskSubmission).where(TaskSubmission.task_id == t.id).order_by(TaskSubmission.version))],
        pending_attachments=[human.attachment_out(a) for a in db.scalars(
            select(TaskAttachment).where(TaskAttachment.task_id == t.id, TaskAttachment.submission_id.is_(None))
            .order_by(TaskAttachment.created_at))],
        limits={"content_chars": human.MAX_CONTENT_CHARS, "notes_chars": human.MAX_NOTES_CHARS,
                "attachments": human.MAX_ATTACHMENTS, "attachment_bytes": get_settings().workforce_attachment_max_bytes,
                "extensions": sorted(human.ALLOWED_EXTENSIONS)})
    if devtasks.is_dev_task(t):
        wt = db.scalar(select(Worktree).where(Worktree.task_id == t.id).order_by(Worktree.id.desc()).limit(1))
        last = scheduler.latest_submission(db, t.id)
        check = dict((last.review or {}).get("dev") or {}) if last else {}
        out["development"] = {
            "files_shown": (t.result or {}).get("files") or [],
            "worktree": ({"id": wt.id, "branch": wt.branch, "base_branch": wt.base_branch, "status": wt.status,
                          "head_commit": wt.head_commit, "modified_files": wt.modified_files,
                          "conflicts": wt.conflicts, "merged_at": wt.merged_at} if wt else None),
            "last_check": check or None, "last_check_version": last.version if last and check else None,
            "merged": (t.result or {}).get("merged")}
    if t.lifecycle in (L.WAITING_FOR_ACCEPTANCE.value, L.ACCEPTED.value):
        agent = db.get(Agent, t.assigned_agent_id) if t.assigned_agent_id else None
        b = scheduler.brief(db, t, agent)
        out["brief"] = {"message": b["single_message"], "sha256": b["sha256"], "chars": len(b["single_message"])}
    return out


@router.post("/tasks/{task_id}/accept")
def accept_task(task_id: int, db: DB, user: User = Depends(require("workforce.work"))) -> dict:
    t = _task(db, task_id, user)
    try:
        human.accept(db, t, user, _perms(user))
    except human.HumanWorkError as e:
        db.rollback()
        raise _fail(e) from e
    return card(db, t, user)


class DeclineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


@router.post("/tasks/{task_id}/decline")
def decline_task(task_id: int, body: DeclineIn, db: DB, user: User = Depends(require("workforce.work"))) -> dict:
    t = _task(db, task_id, user)
    try:
        human.decline(db, t, user, _perms(user), body.reason)
    except human.HumanWorkError as e:
        db.rollback()
        raise _fail(e) from e
    return card(db, t, user)


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=human.MAX_CONTENT_CHARS)
    notes: str = Field(default="", max_length=human.MAX_NOTES_CHARS)
    attachment_ids: list[str] = Field(default_factory=list, max_length=human.MAX_ATTACHMENTS)


@router.post("/tasks/{task_id}/submit", status_code=201)
def submit_task(task_id: int, body: SubmitIn, db: DB, user: User = Depends(require("workforce.work"))) -> dict:
    t = _task(db, task_id, user)
    try:
        sub = human.submit_work(db, t, user, content=body.content, notes=body.notes,
                                attachment_ids=body.attachment_ids)
    except human.HumanWorkError as e:
        db.rollback()
        raise _fail(e) from e
    return submission_out(db, sub)


@router.post("/tasks/{task_id}/attachments", status_code=201)
async def add_attachment(task_id: int, request: Request, db: DB, user: User = Depends(require("workforce.work")),
                         filename: str = Query(min_length=1, max_length=255)) -> dict:
    """Upload one file as the request body (`?filename=…`, `Content-Type` = the file's type)."""
    t = _task(db, task_id, user)
    limit = get_settings().workforce_attachment_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
    chunks, size = [], 0
    async for chunk in request.stream():  # never hold more than the limit in memory
        size += len(chunk)
        if size > limit:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file_too_large")
        chunks.append(chunk)
    try:
        a = human.store_attachment(db, t, user, filename=filename, data=b"".join(chunks),
                                   content_type=request.headers.get("content-type"))
    except human.HumanWorkError as e:
        db.rollback()
        raise _fail(e) from e
    return human.attachment_out(a)


@router.get("/attachments/{attachment_id}")
def download_attachment(attachment_id: str, db: DB, user: User = Depends(require("workforce.read"))) -> Response:
    a = get_scoped(db, TaskAttachment, attachment_id, user)
    path = human.attachment_path(a)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "file_missing")
    log_activity(db, "workforce.attachment_downloaded", category="workforce", organization_id=user.organization_id,
                 user_id=user.id, task_id=a.task_id, details={"attachment_id": a.id})
    db.commit()
    # always a download with a neutral type: an uploaded file is never rendered by the platform's origin
    return FileResponse(path, media_type="application/octet-stream", filename=a.filename,
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                                 "Cache-Control": "private, no-store"})


@router.delete("/attachments/{attachment_id}", status_code=204)
def delete_attachment(attachment_id: str, db: DB, user: User = Depends(require("workforce.work"))) -> None:
    a = get_scoped(db, TaskAttachment, attachment_id, user)
    try:
        human.remove_attachment(db, a, user)
    except human.HumanWorkError as e:
        db.rollback()
        raise _fail(e) from e


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "revise", "reject"]
    note: str = Field(default="", max_length=4000)


@router.post("/tasks/{task_id}/review")
def review_task(task_id: int, body: ReviewIn, db: DB, user: User = Depends(require("workforce.review"))) -> dict:
    t = _task(db, task_id, user)
    if body.decision in ("revise", "reject") and not body.note.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "note_required")
    try:
        review.decide_by_human(db, t, user, body.decision, body.note)
    except review.ReviewError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return card(db, t, user)


@router.post("/tasks/{task_id}/review/retry")
def review_again(task_id: int, db: DB, user: User = Depends(require("workforce.review"))) -> dict:
    t = _task(db, task_id, user)
    try:
        review.ask_manager_again(db, t, user)
    except review.ReviewError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return card(db, t, user)


class ReassignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    worker_id: int
    note: str = Field(min_length=1, max_length=500)


@router.post("/tasks/{task_id}/reassign")
def reassign_task(task_id: int, body: ReassignIn, db: DB, user: User = Depends(require("workforce.review"))) -> dict:
    t = _task(db, task_id, user)
    w = get_scoped(db, WorkforceWorker, body.worker_id, user)
    try:
        control.reassign(db, t, user, w, body.note)
    except control.ControlError as e:
        db.rollback()
        raise _fail(e) from e
    return card(db, t, user)


class NoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=500)


@router.post("/tasks/{task_id}/reopen")
def reopen_task(task_id: int, body: NoteIn, db: DB, user: User = Depends(require("workforce.review"))) -> dict:
    t = _task(db, task_id, user)
    try:
        review.reopen(db, t, user, body.note)
    except review.ReviewError as e:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return card(db, t, user)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: int, body: NoteIn, db: DB, user: User = Depends(require("workforce.review"))) -> dict:
    t = _task(db, task_id, user)
    try:
        control.cancel_task(db, t, user, body.note)
    except control.ControlError as e:
        db.rollback()
        raise _fail(e) from e
    return card(db, t, user)


# ---------------- dashboard ----------------
@router.get("/dashboard")
def dashboard(db: DB, user: User = Depends(require("workforce.read"))) -> dict:
    org = user.organization_id
    force = service.get(db, org)
    perms = _perms(user)
    base = (select(Task).join(Workflow, Workflow.id == Task.workflow_id)
            .where(Task.organization_id == org, Workflow.kind == KIND))
    by_lifecycle = dict(db.execute(select(Task.lifecycle, func.count()).join(Workflow, Workflow.id == Task.workflow_id)
                                   .where(Task.organization_id == org, Workflow.kind == KIND)
                                   .group_by(Task.lifecycle)).all())
    objectives = dict(db.execute(select(Workflow.status, func.count()).where(
        Workflow.organization_id == org, Workflow.kind == KIND).group_by(Workflow.status)).all())
    offered = [t for t in db.scalars(base.where(Task.lifecycle == L.WAITING_FOR_ACCEPTANCE.value))
               if human.can_work(t, service.worker_for_agent(db, t.assigned_agent_id), user, perms)]
    mine = db.scalar(select(func.count()).select_from(Task).where(
        Task.organization_id == org, Task.lifecycle == L.ACCEPTED.value, Task.accepted_by_user_id == user.id)) or 0
    review_queue = [t for t in db.scalars(base.where(Task.lifecycle.in_([L.UNDER_REVIEW.value, L.SUBMITTED.value])))
                    if _needs_human(scheduler.latest_submission(db, t.id))]
    approvals = db.scalar(select(func.count()).select_from(Approval).join(Task, Task.id == Approval.task_id)
                          .join(Workflow, Workflow.id == Task.workflow_id)
                          .where(Approval.organization_id == org, Approval.status == "pending",
                                 Workflow.kind == KIND)) or 0
    bridges = []
    for p in db.scalars(select(Provider).where(Provider.organization_id == org, Provider.kind == bridge.KIND)
                        .order_by(Provider.id)):
        h = p.health or {}
        bridges.append({"id": p.id, "name": p.name, "enabled": p.enabled, "status": p.status, "is_mock": p.is_mock,
                        "terms_acknowledged": bridge.acknowledged(p), "reachable": h.get("reachable"),
                        "ready": h.get("ready"), "usable": bridge.usable(h), "checked_at": h.get("checked_at"),
                        "health_fresh": bridge.health_is_fresh(p),
                        "accounts": len([a for a in h.get("accounts") or [] if not a.get("disabled")]),
                        "queue": h.get("queue")})
    recent = [{"id": a.id, "action": a.action, "actor": a.actor, "level": a.level, "task_id": a.task_id,
               "details": a.details, "at": a.ts} for a in db.scalars(
        select(AuditLog).where(AuditLog.organization_id == org, AuditLog.category == "workforce")
        .order_by(AuditLog.id.desc()).limit(20))]
    latest = [objective_out(db, w) for w in db.scalars(
        select(Workflow).where(Workflow.organization_id == org, Workflow.kind == KIND)
        .order_by(Workflow.id.desc()).limit(5))]
    return {"workforce": service.workforce_out(db, force) if force else None, "bridges": bridges,
            "objectives": {"by_status": objectives, "latest": latest},
            "tasks": {"by_lifecycle": by_lifecycle, "open": sum(n for s, n in by_lifecycle.items()
                                                                if s not in lifecycle.SETTLED)},
            "queues": {"offered_to_me": len(offered), "accepted_by_me": mine, "needs_human_review": len(review_queue),
                       "pending_approvals": approvals},
            "recent": recent}
