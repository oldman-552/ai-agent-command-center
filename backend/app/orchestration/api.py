"""Projects (minimal), workflows (objectives) and tasks API for the orchestrator (M6)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.bus.contract import MessageType, workflow_command
from app.bus.outbox import enqueue
from app.models import Agent, EvaluationResult, Execution, Project, Task, TaskDependency, User, Workflow
from app.models import TaskStatus as T
from app.models.agents import project_agents
from app.orchestration import scheduler
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api", tags=["orchestration"])


# ---------------- projects ----------------
class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=8000)
    mode: Literal["new", "existing", "maintenance"] = "new"
    agent_ids: list[int] = Field(default_factory=list, max_length=200)


def project_out(p: Project, agent_ids: list[int]) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "mode": p.mode, "agent_ids": agent_ids,
            "team_id": p.team_id, "budget_usd": str(p.budget_usd) if p.budget_usd is not None else None,
            "repository_id": p.repository_id, "analyzed_at": p.analyzed_at,
            "created_at": p.created_at, "archived_at": p.archived_at}


def _roster(db, project_id: int) -> list[int]:
    return list(db.scalars(select(project_agents.c.agent_id).where(project_agents.c.project_id == project_id)))


@router.get("/projects")
def list_projects(db: DB, user: User = Depends(require("projects.read"))) -> list[dict]:
    rows = db.scalars(select(Project).where(Project.organization_id == user.organization_id,
                                            Project.archived_at.is_(None)).order_by(Project.id))
    return [project_out(p, _roster(db, p.id)) for p in rows]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, db: DB, user: User = Depends(require("projects.write"))) -> dict:
    if db.scalar(select(Project.id).where(Project.organization_id == user.organization_id, Project.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "name_taken")
    agents = [get_scoped(db, Agent, aid, user) for aid in sorted(set(body.agent_ids))]
    p = Project(organization_id=user.organization_id, name=body.name, description=body.description, mode=body.mode)
    db.add(p)
    db.flush()
    for a in agents:
        db.execute(project_agents.insert().values(project_id=p.id, agent_id=a.id))
    log_activity(db, "projects.created", category="projects", organization_id=user.organization_id, user_id=user.id,
                 project_id=p.id)
    db.commit()
    return project_out(p, [a.id for a in agents])


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str | None = Field(default=None, max_length=8000)
    mode: Literal["new", "existing", "maintenance"] | None = None
    team_id: int | None = None
    budget_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    agent_ids: list[int] | None = Field(default=None, max_length=200)


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectPatch, db: DB, user: User = Depends(require("projects.write"))) -> dict:
    from app.models import Team
    p = get_scoped(db, Project, project_id, user)
    data = body.model_dump(exclude_unset=True)
    if data.get("team_id") is not None:
        get_scoped(db, Team, data["team_id"], user)
    if "budget_usd" in data and not {"budgets.manage"} <= {x.key for x in user.role.permissions}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "budgets_manage_required")
    for k in ("description", "mode", "team_id", "budget_usd"):
        if k in data:
            setattr(p, k, data[k])
    if body.agent_ids is not None:
        agents = [get_scoped(db, Agent, aid, user) for aid in sorted(set(body.agent_ids))]
        db.execute(project_agents.delete().where(project_agents.c.project_id == p.id))
        for a in agents:
            db.execute(project_agents.insert().values(project_id=p.id, agent_id=a.id))
    log_activity(db, "projects.updated", category="projects", organization_id=user.organization_id, user_id=user.id,
                 project_id=p.id, details={k: str(v) for k, v in data.items()})
    db.commit()
    return project_out(p, _roster(db, p.id))


@router.post("/projects/{project_id}/import")
def import_repository(project_id: int, db: DB, user: User = Depends(require("github.manage"))) -> dict:
    """Existing-project mode: clone the linked GitHub repository into the project's workspace (once)."""
    from app.devplane import git as g
    from app.devplane import service as gh
    from app.devplane.worktrees import repo_path
    from app.models import ApiCredential, Repository
    from app.sandbox import runner
    from app.security.crypto import decrypt_secret
    p = get_scoped(db, Project, project_id, user)
    repo = db.get(Repository, p.repository_id) if p.repository_id else None
    if repo is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_linked_repository")
    dest = repo_path(p.organization_id, p.id)
    if (dest / ".git").exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "already_imported")
    cred = db.get(ApiCredential, repo.credential_id) if repo.credential_id else None
    try:
        head = g.clone(dest, gh.remote_url(repo), decrypt_secret(cred.encrypted_value) if cred else None)
    except g.GitError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    runner.ensure_workspace(p.organization_id, p.id)
    if p.mode == "new":
        p.mode = "existing"
    log_activity(db, "projects.imported", category="devplane", organization_id=user.organization_id, user_id=user.id,
                 project_id=p.id, details={"repository": repo.full_name, "head": head})
    db.commit()
    return {"project_id": p.id, "head": head, "mode": p.mode}


@router.post("/projects/{project_id}/analyze")
def analyze_project(project_id: int, db: DB, user: User = Depends(require("projects.write"))) -> dict:
    """OR-09: static repository map (technologies, architecture, dependencies, tests, debt)."""
    from datetime import UTC, datetime

    from app.devplane.worktrees import repo_path
    from app.orchestration.inspector import analyze
    p = get_scoped(db, Project, project_id, user)
    path = repo_path(p.organization_id, p.id)
    if not path.is_dir() or not any(path.iterdir()):
        raise HTTPException(status.HTTP_409_CONFLICT, "no_repository")
    p.analysis, p.analyzed_at = analyze(path), datetime.now(UTC)
    log_activity(db, "projects.analyzed", category="projects", organization_id=user.organization_id, user_id=user.id,
                 project_id=p.id, details={"files": p.analysis["files"],
                                           "languages": [x["name"] for x in p.analysis["technologies"]["languages"]]})
    db.commit()
    return p.analysis


@router.get("/projects/{project_id}/analysis")
def get_analysis(project_id: int, db: DB, user: User = Depends(require("projects.read"))) -> dict:
    p = get_scoped(db, Project, project_id, user)
    if p.analysis is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_analyzed")
    return p.analysis


# ---------------- workflows ----------------
class WorkflowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: int
    title: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=20000)
    idempotency_key: str | None = Field(default=None, max_length=128)


def workflow_out(db, wf: Workflow, with_tasks: bool = False) -> dict:
    out = {"id": wf.id, "project_id": wf.project_id, "title": wf.title, "objective": wf.objective,
           "status": wf.status, "error": wf.error, "plan": wf.plan, "created_at": wf.created_at,
           "updated_at": wf.updated_at}
    if with_tasks:
        tasks = list(db.scalars(select(Task).where(Task.workflow_id == wf.id).order_by(Task.id)))
        deps = db.scalars(select(TaskDependency).where(TaskDependency.task_id.in_([t.id for t in tasks]))).all()
        out["tasks"] = [task_out(t) for t in tasks]
        out["dependencies"] = [{"task_id": d.task_id, "depends_on_id": d.depends_on_id} for d in deps]
    return out


@router.post("/workflows", status_code=201)
def submit_objective(body: WorkflowIn, db: DB, user: User = Depends(require("workflows.run"))) -> dict:
    project = get_scoped(db, Project, body.project_id, user)
    key = f"workflow:{user.organization_id}:{body.idempotency_key}" if body.idempotency_key else None
    if key:  # same submission twice → same workflow, never a second plan
        existing = db.scalar(select(Workflow).where(Workflow.thread_id == key))
        if existing:
            return workflow_out(db, existing)
    wf = Workflow(organization_id=user.organization_id, project_id=project.id, title=body.title,
                  objective=body.objective, status="planning", created_by_user_id=user.id,
                  thread_id=key or f"workflow:{uuid.uuid4()}")
    db.add(wf)
    db.flush()
    enqueue(db, workflow_command(MessageType.WORKFLOW_PLAN, workflow_id=wf.id, organization_id=wf.organization_id,
                                 project_id=wf.project_id, sender=f"user:{user.id}", reason="submitted",
                                 idempotency_key=f"workflow.plan:{wf.id}"))
    log_activity(db, "workflows.submitted", category="orchestration", organization_id=user.organization_id,
                 user_id=user.id, project_id=project.id, details={"workflow_id": wf.id})
    db.commit()
    return workflow_out(db, wf)


@router.get("/workflows")
def list_workflows(db: DB, user: User = Depends(require("tasks.read")), project_id: int | None = None,
                   limit: int = Query(default=50, le=200)) -> list[dict]:
    stmt = select(Workflow).where(Workflow.organization_id == user.organization_id)
    if project_id:
        stmt = stmt.where(Workflow.project_id == project_id)
    return [workflow_out(db, w) for w in db.scalars(stmt.order_by(Workflow.id.desc()).limit(limit))]


@router.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: int, db: DB, user: User = Depends(require("tasks.read"))) -> dict:
    return workflow_out(db, get_scoped(db, Workflow, workflow_id, user), with_tasks=True)


@router.post("/workflows/{workflow_id}/advance")
def nudge(workflow_id: int, db: DB, user: User = Depends(require("tasks.control"))) -> dict:
    """Ask the orchestrator to look again (e.g. after adding an agent with a missing skill)."""
    wf = get_scoped(db, Workflow, workflow_id, user)
    if wf.status != "running":
        raise HTTPException(status.HTTP_409_CONFLICT, f"workflow_not_running:{wf.status}")
    scheduler.request_advance(db, wf, f"nudged by user:{user.id}")
    db.commit()
    return workflow_out(db, wf)


@router.post("/workflows/{workflow_id}/cancel")
def cancel_workflow(workflow_id: int, db: DB, user: User = Depends(require("tasks.control"))) -> dict:
    from app.runtime import service

    wf = get_scoped(db, Workflow, workflow_id, user)
    if wf.status in ("completed", "failed", "cancelled"):
        return workflow_out(db, wf)
    if wf.kind == "workforce":  # same effect, recorded in the task history with the person's name
        from app.workforce import control
        return workflow_out(db, control.cancel_objective(db, wf, user, "cancelled from the workflows page"))
    wf = db.get(Workflow, wf.id, with_for_update=True, populate_existing=True)
    for t in db.scalars(select(Task).where(Task.workflow_id == wf.id, Task.status.not_in(scheduler.TERMINAL_TASK))):
        for ex in db.scalars(select(Execution).where(Execution.task_id == t.id)):
            service.request_cancel(db, ex, f"user:{user.id}")
        t.status = T.CANCELLED.value
    wf.status = "cancelled"
    log_activity(db, "workflows.cancelled", category="orchestration", organization_id=user.organization_id,
                 user_id=user.id, project_id=wf.project_id, details={"workflow_id": wf.id})
    db.commit()
    return workflow_out(db, wf)


# ---------------- tasks ----------------
def task_out(t: Task) -> dict:
    cols = ["id", "project_id", "workflow_id", "title", "description", "task_type", "required_skills", "status",
            "priority", "assigned_agent_id", "acceptance_criteria", "evidence", "review_status", "result", "error",
            "attempts", "max_attempts", "estimated_effort", "critical_operation", "started_at", "finished_at", "created_at", "updated_at"]
    out = {c: getattr(t, c) for c in cols}
    out["budget_usd"] = str(t.budget_usd) if t.budget_usd is not None else None
    return out


@router.get("/tasks")
def list_tasks(db: DB, user: User = Depends(require("tasks.read")), workflow_id: int | None = None,
               project_id: int | None = None, status_: str | None = Query(default=None, alias="status"),
               limit: int = Query(default=200, le=500)) -> list[dict]:
    stmt = select(Task).where(Task.organization_id == user.organization_id)
    if workflow_id:
        stmt = stmt.where(Task.workflow_id == workflow_id)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if status_:
        stmt = stmt.where(Task.status == status_)
    return [task_out(t) for t in db.scalars(stmt.order_by(Task.id).limit(limit))]


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: DB, user: User = Depends(require("tasks.read"))) -> dict:
    t = get_scoped(db, Task, task_id, user)
    deps = db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == t.id)).all()
    execs = db.scalars(select(Execution).where(Execution.task_id == t.id).order_by(Execution.created_at)).all()
    evals = db.scalars(select(EvaluationResult).where(EvaluationResult.task_id == t.id)
                       .order_by(EvaluationResult.id)).all()
    return {**task_out(t), "depends_on": list(deps),
            "evaluations": [{"id": e.id, "execution_id": e.execution_id, "decision": e.decision, "overall": e.overall,
                             "scores": e.scores, "evidence": e.evidence, "created_at": e.created_at} for e in evals],
            "executions": [{"id": e.id, "status": e.status, "agent_id": e.agent_id, "is_mock": e.is_mock,
                            "created_at": e.created_at, "ended_at": e.ended_at} for e in execs]}


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "revise", "reject"]
    note: str = Field(default="", max_length=4000)


@router.post("/tasks/{task_id}/review")
def review_task(task_id: int, body: ReviewIn, db: DB, user: User = Depends(require("tasks.control"))) -> dict:
    t = get_scoped(db, Task, task_id, user)
    if body.decision in ("revise", "reject") and not body.note.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "note_required")
    return task_out(scheduler.review(db, t, body.decision, body.note, f"user:{user.id}"))
