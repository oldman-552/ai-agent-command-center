"""Memory & context API (MEM-01..04, CX-01): browse, search, correct, verify and delete memories; preview the
context an agent would receive for a task."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.memory import store
from app.models import Agent, Memory, Organization, Project, Task, TaskDependency, Team, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/memory", tags=["memory"])
MemType = Literal["agent_identity", "project", "task", "conversation", "technical_decision", "codebase", "execution",
                  "lesson", "team_shared"]


def out(m: Memory, score: float | None = None) -> dict:
    d = {"id": m.id, "memory_type": m.memory_type, "title": m.title, "content": m.content, "source": m.source,
         "verification": m.verification, "version": m.version, "supersedes_id": m.supersedes_id,
         "is_current": m.is_current, "project_id": m.project_id, "agent_id": m.agent_id, "team_id": m.team_id,
         "task_id": m.task_id, "expires_at": m.expires_at, "created_at": m.created_at, "updated_at": m.updated_at}
    if score is not None:
        d["score"] = round(score, 4)
    return d


def _scope(db, user: User, project_id: int | None, agent_id: int | None, team_id: int | None) -> None:
    for model, oid in ((Project, project_id), (Agent, agent_id), (Team, team_id)):
        if oid is not None:
            get_scoped(db, model, oid, user)


class MemoryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_type: MemType
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=20_000)
    verification: Literal["verified", "assumption", "suggestion"] = "verified"
    project_id: int | None = None
    agent_id: int | None = None
    team_id: int | None = None
    task_id: int | None = None
    expires_at: datetime | None = None


class CorrectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=20_000)
    title: str | None = Field(default=None, max_length=255)
    verification: Literal["verified", "assumption"] = "verified"


@router.get("")
def list_memories(db: DB, user: User = Depends(require("memory.read")), project_id: int | None = None,
                  memory_type: MemType | None = None, verification: str | None = None,
                  limit: int = Query(default=200, le=500)) -> list[dict]:
    _scope(db, user, project_id, None, None)
    stmt = select(Memory).where(Memory.organization_id == user.organization_id, Memory.is_current.is_(True))
    if project_id is not None:
        stmt = stmt.where(Memory.project_id == project_id)
    if memory_type:
        stmt = stmt.where(Memory.memory_type == memory_type)
    if verification:
        stmt = stmt.where(Memory.verification == verification)
    return [out(m) for m in db.scalars(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()).limit(limit))]


@router.get("/search")
def search(db: DB, q: str = Query(min_length=1, max_length=2000), project_id: int | None = None,
           agent_id: int | None = None, team_id: int | None = None, limit: int = Query(default=10, le=50),
           user: User = Depends(require("memory.read"))) -> list[dict]:
    _scope(db, user, project_id, agent_id, team_id)
    if project_id is None and agent_id is None and team_id is None:
        raise HTTPException(422, "scope_required")
    hits = store.search(db, organization_id=user.organization_id, query=q, project_id=project_id,
                        agent_id=agent_id, team_id=team_id, limit=limit)
    return [{**out(h.memory, h.score), "match": h.method} for h in hits]


class EmbeddingIn(BaseModel):
    provider_id: int | None = None  # null turns semantic embeddings off
    model: str | None = Field(default=None, max_length=128)


def _embedding_state(db, organization_id: int) -> dict:
    from app.memory import semantic
    cfg = semantic.get_config(db, organization_id)
    return {"enabled": cfg is not None, "provider_id": cfg["provider_id"] if cfg else None,
            "model": cfg["model"] if cfg else None, "pending": semantic.pending(db, organization_id),
            "supported_kinds": sorted(semantic.KINDS), "fallback": "hashed"}


@router.get("/embedding")
def get_embedding(db: DB, user: User = Depends(require("memory.read"))) -> dict:
    return _embedding_state(db, user.organization_id)


@router.put("/embedding")
def set_embedding(body: EmbeddingIn, db: DB, user: User = Depends(require("settings.manage"))) -> dict:
    """Choosing a model sends memory text to that provider (none for local servers); audited."""
    from app.memory import semantic
    org = db.get(Organization, user.organization_id)
    try:
        cfg = semantic.set_config(db, org, body.provider_id, body.model)
    except semantic.EmbeddingError as e:
        raise HTTPException(422, str(e)) from e
    log_activity(db, "memory.embedding_configured", category="memory", organization_id=org.id,
                 actor=f"user:{user.id}", details={"provider_id": body.provider_id, "model": body.model,
                                                   "enabled": cfg is not None})
    db.commit()
    semantic.reset()
    return _embedding_state(db, user.organization_id)


@router.post("/embedding/reindex")
def reindex(db: DB, user: User = Depends(require("settings.manage"))) -> dict:
    """Index up to 200 memories now (the worker also does this in the background)."""
    from app.memory import semantic
    if semantic.get_config(db, user.organization_id) is None:
        raise HTTPException(409, "semantic embeddings are not configured")
    db.commit()
    res = semantic.backfill(user.organization_id)
    return {**res, **_embedding_state(db, user.organization_id)}


@router.post("", status_code=201)
def create(body: MemoryIn, db: DB, user: User = Depends(require("memory.write"))) -> dict:
    _scope(db, user, body.project_id, body.agent_id, body.team_id)
    if body.task_id is not None:
        t = get_scoped(db, Task, body.task_id, user)
        if t.project_id != body.project_id:
            raise HTTPException(422, "task_not_in_project")
    m = store.create(db, organization_id=user.organization_id, source={"kind": "user", "ref": f"user:{user.id}"},
                     **body.model_dump())
    db.commit()
    return out(m)


@router.get("/{memory_id}")
def get(memory_id: int, db: DB, user: User = Depends(require("memory.read"))) -> dict:
    m = get_scoped(db, Memory, memory_id, user)
    return {**out(m), "history": [out(x) for x in store.history(db, m)[1:]]}


@router.post("/{memory_id}/correct")
def correct(memory_id: int, body: CorrectIn, db: DB, user: User = Depends(require("memory.write"))) -> dict:
    m = get_scoped(db, Memory, memory_id, user)
    new = store.correct(db, m, content=body.content, title=body.title, user_id=user.id,
                        verification=body.verification)
    db.commit()
    return out(new)


@router.post("/{memory_id}/verify")
def verify(memory_id: int, db: DB, user: User = Depends(require("memory.write"))) -> dict:
    m = get_scoped(db, Memory, memory_id, user)
    if not m.is_current:
        raise HTTPException(409, "not_current_version")
    store.verify(db, m, user.id)
    db.commit()
    return out(m)


@router.delete("/{memory_id}")
def delete(memory_id: int, db: DB, user: User = Depends(require("memory.write"))) -> dict:
    m = get_scoped(db, Memory, memory_id, user)
    if not m.is_current:
        raise HTTPException(409, "not_current_version")
    n = store.delete(db, m, user.id)
    db.commit()
    return {"deleted": n}


context_router = APIRouter(prefix="/api/context", tags=["memory"])


@context_router.get("/tasks/{task_id}")
def preview(task_id: int, db: DB, user: User = Depends(require("memory.read")),
            agent_id: int | None = None) -> dict:
    """The context the platform would assemble for this task now (prompt + manifest; system policy shown too)."""
    from app.context.service import for_task
    task = get_scoped(db, Task, task_id, user)
    aid = agent_id or task.assigned_agent_id
    agent = get_scoped(db, Agent, aid, user) if aid else None
    dep_ids = db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task.id)).all()
    deps = [db.get(Task, d) for d in dep_ids]
    ctx = for_task(db, task, [d for d in deps if d is not None], agent)
    return {"task_id": task.id, "agent_id": agent.id if agent else None, "system": ctx.system, "prompt": ctx.prompt,
            "manifest": ctx.manifest}
