"""Dynamic agent management (spec Phase 3): CRUD, clone, enable/disable, start/stop, archive, delete,
templates, config versions, lifecycle history. Agents are data — nothing here is hardcoded per agent."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.control.lifecycle import BUSY, transition
from app.models import (
    Agent,
    AgentConfigVersion,
    AgentState,
    AgentStateTransition,
    AgentTemplate,
    ExecutionEngineKind,
    Project,
    Provider,
    SecurityProfile,
    User,
)
from app.runtime.tools import TOOLS, disallowed_tools
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api", tags=["agents"])

CONFIG_FIELDS = ["name", "description", "role", "avatar", "template_id", "system_instructions", "provider_id", "model",
                 "fallback_provider_id", "fallback_model", "execution_engine", "tool_permissions", "skills",
                 "mcp_server_ids", "memory_profile", "context_policy", "security_profile", "token_budget",
                 "cost_budget_usd", "timeout_seconds", "retry_policy", "concurrency_limit", "priority",
                 "supervisor_agent_id"]


class RetryPolicy(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: float = Field(default=5, ge=0, le=3600)
    backoff_multiplier: float = Field(default=2.0, ge=1, le=10)


class AgentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    role: str = Field(min_length=1, max_length=64)
    avatar: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_-]+$")
    template_id: int | None = None
    system_instructions: str = Field(default="", max_length=50_000)
    provider_id: int | None = None
    model: str | None = Field(default=None, max_length=128)
    fallback_provider_id: int | None = None
    fallback_model: str | None = Field(default=None, max_length=128)
    execution_engine: ExecutionEngineKind = ExecutionEngineKind.API
    tool_permissions: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    mcp_server_ids: list[int] = Field(default_factory=list)
    memory_profile: dict[str, Any] = Field(default_factory=dict)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    security_profile: SecurityProfile = SecurityProfile.DEVELOPER
    project_ids: list[int] = Field(default_factory=list)
    token_budget: int | None = Field(default=None, ge=1)
    cost_budget_usd: Decimal | None = Field(default=None, ge=0)
    timeout_seconds: int = Field(default=900, ge=1, le=86_400)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    concurrency_limit: int = Field(default=1, ge=1, le=64)
    priority: int = Field(default=5, ge=1, le=10)
    supervisor_agent_id: int | None = None

    @field_validator("tool_permissions")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return sorted(set(v))


class AgentCreate(AgentBase):
    identifier: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")


class AgentPatch(BaseModel):
    """Partial update: any subset of AgentBase fields."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    role: str | None = Field(default=None, min_length=1, max_length=64)
    avatar: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9_-]+$")
    system_instructions: str | None = Field(default=None, max_length=50_000)
    provider_id: int | None = None
    model: str | None = None
    fallback_provider_id: int | None = None
    fallback_model: str | None = None
    execution_engine: ExecutionEngineKind | None = None
    tool_permissions: list[str] | None = None
    skills: list[str] | None = None
    mcp_server_ids: list[int] | None = None
    memory_profile: dict[str, Any] | None = None
    context_policy: dict[str, Any] | None = None
    security_profile: SecurityProfile | None = None
    project_ids: list[int] | None = None
    token_budget: int | None = Field(default=None, ge=1)
    cost_budget_usd: Decimal | None = Field(default=None, ge=0)
    timeout_seconds: int | None = Field(default=None, ge=1, le=86_400)
    retry_policy: RetryPolicy | None = None
    concurrency_limit: int | None = Field(default=None, ge=1, le=64)
    priority: int | None = Field(default=None, ge=1, le=10)
    supervisor_agent_id: int | None = None


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    identifier: str
    name: str
    description: str
    role: str
    avatar: str | None
    template_id: int | None
    system_instructions: str
    provider_id: int | None
    model: str | None
    fallback_provider_id: int | None
    fallback_model: str | None
    execution_engine: str
    tool_permissions: list[str]
    skills: list[str]
    mcp_server_ids: list[int]
    memory_profile: dict
    context_policy: dict
    security_profile: str
    project_ids: list[int]
    token_budget: int | None
    cost_budget_usd: Decimal | None
    timeout_seconds: int
    retry_policy: dict
    concurrency_limit: int
    priority: int
    supervisor_agent_id: int | None
    state: str
    is_enabled: bool
    archived_at: datetime | None
    config_version: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


def agent_out(a: Agent) -> AgentOut:
    data = {c: getattr(a, c) for c in AgentOut.model_fields if c != "project_ids"}
    return AgentOut(**data, project_ids=sorted(p.id for p in a.projects))


def _snapshot(a: Agent) -> dict:
    snap = {f: getattr(a, f) for f in CONFIG_FIELDS}
    snap["cost_budget_usd"] = str(a.cost_budget_usd) if a.cost_budget_usd is not None else None
    snap["project_ids"] = sorted(p.id for p in a.projects)
    return snap


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:48] or "agent"


def _unique_identifier(db: Session, org_id: int, base: str) -> str:
    ident, n = base, 2
    while db.scalar(select(Agent.id).where(Agent.organization_id == org_id, Agent.identifier == ident)):
        ident, n = f"{base}-{n}", n + 1
    return ident


def _validate(db: Session, user: User, values: dict, agent_id: int | None = None) -> None:
    org = user.organization_id
    for key in ("provider_id", "fallback_provider_id"):
        if values.get(key) is not None:
            p = db.get(Provider, values[key])
            if not p or p.organization_id != org:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid_{key}")
    if values.get("template_id") is not None:
        t = db.get(AgentTemplate, values["template_id"])
        if not t or (t.organization_id not in (None, org)):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_template_id")
    sup = values.get("supervisor_agent_id")
    if sup is not None:
        s = db.get(Agent, sup)
        if not s or s.organization_id != org or s.id == agent_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_supervisor_agent_id")
    profile = values.get("security_profile")
    profile = profile.value if hasattr(profile, "value") else profile
    bad = disallowed_tools(profile, values.get("tool_permissions") or [])
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"tools_not_allowed_for_profile:{','.join(bad)}")


def _set_projects(db: Session, user: User, agent: Agent, ids: list[int]) -> None:
    projects = [get_scoped(db, Project, pid, user) for pid in set(ids)]
    agent.projects = projects


def _record_version(db: Session, agent: Agent, user: User) -> None:
    db.add(AgentConfigVersion(agent_id=agent.id, version=agent.config_version, snapshot=_snapshot(agent),
                              changed_by_user_id=user.id))


def _load(db: Session, agent_id: int, user: User) -> Agent:
    return get_scoped(db, Agent, agent_id, user)


# ---------------- agents ----------------
@router.get("/agents", response_model=list[AgentOut])
def list_agents(db: DB, user: User = Depends(require("agents.read")), state: str | None = None,
                enabled: bool | None = None, archived: bool = False, role: str | None = None,
                q: str | None = Query(default=None, max_length=100)) -> list[AgentOut]:
    stmt = select(Agent).where(Agent.organization_id == user.organization_id)
    stmt = stmt.where(Agent.archived_at.is_not(None) if archived else Agent.archived_at.is_(None))
    if state:
        stmt = stmt.where(Agent.state == state)
    if enabled is not None:
        stmt = stmt.where(Agent.is_enabled.is_(enabled))
    if role:
        stmt = stmt.where(Agent.role == role)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Agent.name.ilike(like), Agent.identifier.ilike(like), Agent.description.ilike(like)))
    return [agent_out(a) for a in db.scalars(stmt.order_by(Agent.priority.desc(), Agent.id))]


@router.post("/agents", response_model=AgentOut, status_code=201)
def create_agent(body: AgentCreate, db: DB, user: User = Depends(require("agents.write"))) -> AgentOut:
    values = body.model_dump(exclude={"identifier", "project_ids"})
    _validate(db, user, values)
    ident = body.identifier or _slug(body.name)
    if body.identifier and db.scalar(select(Agent.id).where(Agent.organization_id == user.organization_id,
                                                             Agent.identifier == ident)):
        raise HTTPException(status.HTTP_409_CONFLICT, "identifier_taken")
    ident = _unique_identifier(db, user.organization_id, ident)
    values["retry_policy"] = body.retry_policy.model_dump()
    agent = Agent(organization_id=user.organization_id, identifier=ident, state=AgentState.CREATED.value, **values)
    db.add(agent)
    db.flush()
    _set_projects(db, user, agent, body.project_ids)
    db.add(AgentStateTransition(agent_id=agent.id, from_state=None, to_state=AgentState.CREATED.value,
                                actor=f"user:{user.id}", reason="created"))
    _record_version(db, agent, user)
    log_activity(db, "agents.created", category="agents", user_id=user.id, agent_id=agent.id,
                 organization_id=user.organization_id, details={"identifier": ident, "role": agent.role})
    db.commit()
    return agent_out(agent)


@router.get("/agents/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: int, db: DB, user: User = Depends(require("agents.read"))) -> AgentOut:
    return agent_out(_load(db, agent_id, user))


@router.patch("/agents/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, body: AgentPatch, db: DB, user: User = Depends(require("agents.write"))) -> AgentOut:
    agent = _load(db, agent_id, user)
    if agent.archived_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "agent_archived")
    changes = body.model_dump(exclude_unset=True)
    project_ids = changes.pop("project_ids", None)
    merged = {f: getattr(agent, f) for f in CONFIG_FIELDS} | changes
    _validate(db, user, merged, agent_id=agent.id)
    for k, v in changes.items():
        setattr(agent, k, v.model_dump() if hasattr(v, "model_dump") else (v.value if hasattr(v, "value") else v))
    if project_ids is not None:
        _set_projects(db, user, agent, project_ids)
    if changes or project_ids is not None:
        agent.config_version += 1
        db.flush()
        _record_version(db, agent, user)
        log_activity(db, "agents.updated", category="agents", user_id=user.id, agent_id=agent.id,
                     organization_id=user.organization_id,
                     details={"fields": sorted(changes) + (["project_ids"] if project_ids is not None else []),
                              "version": agent.config_version})
    db.commit()
    return agent_out(agent)


@router.post("/agents/{agent_id}/clone", response_model=AgentOut, status_code=201)
def clone_agent(agent_id: int, db: DB, user: User = Depends(require("agents.write"))) -> AgentOut:
    src = _load(db, agent_id, user)
    values = {f: getattr(src, f) for f in CONFIG_FIELDS}
    values["name"] = f"{src.name} (copy)"[:128]
    ident = _unique_identifier(db, user.organization_id, f"{src.identifier}-copy"[:56])
    agent = Agent(organization_id=user.organization_id, identifier=ident, state=AgentState.CREATED.value, **values)
    db.add(agent)
    db.flush()
    agent.projects = list(src.projects)
    db.add(AgentStateTransition(agent_id=agent.id, to_state=AgentState.CREATED.value, actor=f"user:{user.id}",
                                reason=f"cloned from {src.id}"))
    _record_version(db, agent, user)
    log_activity(db, "agents.cloned", category="agents", user_id=user.id, agent_id=agent.id,
                 organization_id=user.organization_id, details={"source_agent_id": src.id})
    db.commit()
    return agent_out(agent)


def _control(db: Session, agent_id: int, user: User, action: str) -> Agent:
    agent = _load(db, agent_id, user)
    actor = f"user:{user.id}"
    if agent.archived_at and action != "restore":
        raise HTTPException(status.HTTP_409_CONFLICT, "agent_archived")
    st = AgentState(agent.state)
    if action == "enable":
        agent.is_enabled = True
        if st == AgentState.DISABLED:
            transition(db, agent, AgentState.INITIALIZING, actor=actor, reason="enabled")
            transition(db, agent, AgentState.IDLE, actor=actor, reason="initialized")
    elif action == "disable":
        if st in BUSY:
            raise HTTPException(status.HTTP_409_CONFLICT, "agent_busy_stop_first")
        agent.is_enabled = False
        transition(db, agent, AgentState.DISABLED, actor=actor, reason="disabled")
    elif action == "start":
        if not agent.is_enabled:
            raise HTTPException(status.HTTP_409_CONFLICT, "agent_disabled")
        if st in (AgentState.CREATED, AgentState.FAILED, AgentState.DISABLED):
            transition(db, agent, AgentState.INITIALIZING, actor=actor, reason="start requested")
        if AgentState(agent.state) == AgentState.INITIALIZING or AgentState(agent.state) in (AgentState.COMPLETED, AgentState.CANCELLED):
            transition(db, agent, AgentState.IDLE, actor=actor, reason="ready")
    elif action == "stop":
        # cancels current work; running executions observe cancellation through the runtime (M2)
        if st in BUSY:
            transition(db, agent, AgentState.CANCELLED, actor=actor, reason="stop requested")
        if AgentState(agent.state) in (AgentState.CANCELLED, AgentState.COMPLETED):
            transition(db, agent, AgentState.IDLE, actor=actor, reason="stopped")
    elif action == "archive":
        if st in BUSY:
            raise HTTPException(status.HTTP_409_CONFLICT, "agent_busy_stop_first")
        agent.archived_at = datetime.now(UTC)
        agent.is_enabled = False
        if st != AgentState.DISABLED:
            transition(db, agent, AgentState.DISABLED, actor=actor, reason="archived")
    elif action == "restore":
        agent.archived_at = None
    log_activity(db, f"agents.{action}", category="agents", user_id=user.id, agent_id=agent.id,
                 organization_id=user.organization_id)
    db.commit()
    return agent


@router.post("/agents/{agent_id}/enable", response_model=AgentOut)
def enable(agent_id: int, db: DB, user: User = Depends(require("agents.control"))) -> AgentOut:
    return agent_out(_control(db, agent_id, user, "enable"))


@router.post("/agents/{agent_id}/disable", response_model=AgentOut)
def disable(agent_id: int, db: DB, user: User = Depends(require("agents.control"))) -> AgentOut:
    return agent_out(_control(db, agent_id, user, "disable"))


@router.post("/agents/{agent_id}/start", response_model=AgentOut)
def start(agent_id: int, db: DB, user: User = Depends(require("agents.control"))) -> AgentOut:
    return agent_out(_control(db, agent_id, user, "start"))


@router.post("/agents/{agent_id}/stop", response_model=AgentOut)
def stop(agent_id: int, db: DB, user: User = Depends(require("agents.control"))) -> AgentOut:
    return agent_out(_control(db, agent_id, user, "stop"))


@router.post("/agents/{agent_id}/archive", response_model=AgentOut)
def archive(agent_id: int, db: DB, user: User = Depends(require("agents.delete"))) -> AgentOut:
    return agent_out(_control(db, agent_id, user, "archive"))


@router.post("/agents/{agent_id}/restore", response_model=AgentOut)
def restore(agent_id: int, db: DB, user: User = Depends(require("agents.delete"))) -> AgentOut:
    return agent_out(_control(db, agent_id, user, "restore"))


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: int, db: DB, user: User = Depends(require("agents.delete"))) -> None:
    """Hard delete is only allowed for archived agents (two-step, recoverable until then)."""
    agent = _load(db, agent_id, user)
    if not agent.archived_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "archive_first")
    log_activity(db, "agents.deleted", category="agents", level="warning", user_id=user.id,
                 organization_id=user.organization_id,
                 details={"agent_id": agent.id, "identifier": agent.identifier, "snapshot": _snapshot(agent)})
    db.delete(agent)
    db.commit()


@router.get("/agents/{agent_id}/versions")
def versions(agent_id: int, db: DB, user: User = Depends(require("agents.read"))) -> list[dict]:
    _load(db, agent_id, user)
    rows = db.scalars(select(AgentConfigVersion).where(AgentConfigVersion.agent_id == agent_id)
                      .order_by(AgentConfigVersion.version.desc()))
    return [{"version": v.version, "snapshot": v.snapshot, "changed_by_user_id": v.changed_by_user_id,
             "created_at": v.created_at} for v in rows]


@router.get("/agents/{agent_id}/transitions")
def transitions(agent_id: int, db: DB, user: User = Depends(require("agents.read")),
                limit: int = Query(default=100, le=500)) -> list[dict]:
    _load(db, agent_id, user)
    rows = db.scalars(select(AgentStateTransition).where(AgentStateTransition.agent_id == agent_id)
                      .order_by(AgentStateTransition.id.desc()).limit(limit))
    return [{"from": t.from_state, "to": t.to_state, "actor": t.actor, "reason": t.reason, "ts": t.ts} for t in rows]


# ---------------- templates ----------------
class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_]+$")
    name_ar: str = Field(min_length=1, max_length=128)
    name_en: str = Field(min_length=1, max_length=128)
    role: str = Field(min_length=1, max_length=64)
    description_ar: str = ""
    description_en: str = ""
    system_instructions: str = Field(default="", max_length=50_000)
    tool_permissions: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    security_profile: SecurityProfile = SecurityProfile.DEVELOPER
    context_policy: dict[str, Any] = Field(default_factory=dict)
    memory_profile: dict[str, Any] = Field(default_factory=dict)


class TemplatePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name_ar: str | None = None
    name_en: str | None = None
    role: str | None = None
    description_ar: str | None = None
    description_en: str | None = None
    system_instructions: str | None = None
    tool_permissions: list[str] | None = None
    skills: list[str] | None = None
    security_profile: SecurityProfile | None = None
    context_policy: dict[str, Any] | None = None
    memory_profile: dict[str, Any] | None = None


def _template_out(t: AgentTemplate) -> dict:
    return {c.name: getattr(t, c.name) for c in AgentTemplate.__table__.columns}


@router.get("/agent-templates")
def list_templates(db: DB, user: User = Depends(require("agents.read"))) -> list[dict]:
    rows = db.scalars(select(AgentTemplate).where(or_(AgentTemplate.organization_id.is_(None),
                                                      AgentTemplate.organization_id == user.organization_id))
                      .order_by(AgentTemplate.id))
    return [_template_out(t) for t in rows]


@router.post("/agent-templates", status_code=201)
def create_template(body: TemplateIn, db: DB, user: User = Depends(require("templates.write"))) -> dict:
    bad = disallowed_tools(body.security_profile.value, body.tool_permissions)
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"tools_not_allowed_for_profile:{','.join(bad)}")
    if db.scalar(select(AgentTemplate.id).where(AgentTemplate.key == body.key)):
        raise HTTPException(status.HTTP_409_CONFLICT, "key_taken")
    t = AgentTemplate(organization_id=user.organization_id, is_system=False,
                      **body.model_dump(exclude={"security_profile"}), security_profile=body.security_profile.value)
    db.add(t)
    log_activity(db, "templates.created", category="agents", user_id=user.id, organization_id=user.organization_id,
                 details={"key": body.key})
    db.commit()
    return _template_out(t)


@router.patch("/agent-templates/{template_id}")
def update_template(template_id: int, body: TemplatePatch, db: DB,
                    user: User = Depends(require("templates.write"))) -> dict:
    t = db.get(AgentTemplate, template_id)
    if not t or t.organization_id not in (None, user.organization_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    changes = body.model_dump(exclude_unset=True)
    profile = changes.get("security_profile", t.security_profile)
    profile = profile.value if hasattr(profile, "value") else profile
    bad = disallowed_tools(profile, changes.get("tool_permissions", t.tool_permissions))
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"tools_not_allowed_for_profile:{','.join(bad)}")
    for k, v in changes.items():
        setattr(t, k, v.value if hasattr(v, "value") else v)
    log_activity(db, "templates.updated", category="agents", user_id=user.id, organization_id=user.organization_id,
                 details={"template_id": t.id, "fields": sorted(changes)})
    db.commit()
    return _template_out(t)


@router.post("/agent-templates/{template_id}/instantiate", response_model=AgentOut, status_code=201)
def instantiate(template_id: int, db: DB, user: User = Depends(require("agents.write")),
                name: str | None = Query(default=None, max_length=128)) -> AgentOut:
    t = db.get(AgentTemplate, template_id)
    if not t or t.organization_id not in (None, user.organization_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    body = AgentCreate(name=name or t.name_en, role=t.role, description=t.description_en, template_id=t.id,
                       system_instructions=t.system_instructions, tool_permissions=t.tool_permissions,
                       skills=t.skills, security_profile=SecurityProfile(t.security_profile),
                       context_policy=t.context_policy, memory_profile=t.memory_profile)
    return create_agent(body, db, user)


@router.get("/tools")
def list_tools(_: User = Depends(require("agents.read"))) -> dict:
    from app.runtime.tools import PROFILE_CEILING
    return {"tools": TOOLS, "profile_ceiling": {k: sorted(v) for k, v in PROFILE_CEILING.items()}}


@router.get("/agents-stats")
def agent_stats(db: DB, user: User = Depends(require("agents.read"))) -> dict:
    rows = db.execute(select(Agent.state, func.count()).where(Agent.organization_id == user.organization_id,
                                                               Agent.archived_at.is_(None)).group_by(Agent.state))
    return dict(rows.all())
