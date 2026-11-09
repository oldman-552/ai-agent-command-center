"""Projects (minimal), workflows (objectives) and tasks API for the orchestrator (M6)."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.bus.contract import MessageType, workflow_command
from app.bus.outbox import enqueue
from app.models import Agent, Execution, Project, Task, TaskDependency, User, Workflow
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
            "attempts", "max_attempts", "estimated_effort", "started_at", "finished_at", "created_at", "updated_at"]
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
    return {**task_out(t), "depends_on": list(deps),
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
