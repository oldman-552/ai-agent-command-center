"""Agent teams (AG-07): a named group of agents and humans with an optional lead agent and policies.

Teams scope `team_shared` memories and can be attached to projects; membership changes are audited. Only members of
the caller's organization can be added.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, insert, select

from app.models import Agent, Project, Team, User, team_agents, team_users
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/teams", tags=["teams"])


def _members(db, team_id: int) -> tuple[list[int], list[int]]:
    agents = list(db.scalars(select(team_agents.c.agent_id).where(team_agents.c.team_id == team_id)))
    users = list(db.scalars(select(team_users.c.user_id).where(team_users.c.team_id == team_id)))
    return sorted(agents), sorted(users)


def team_out(db, t: Team) -> dict[str, Any]:
    agent_ids, user_ids = _members(db, t.id)
    projects = list(db.scalars(select(Project.id).where(Project.team_id == t.id)))
    return {"id": t.id, "name": t.name, "description": t.description, "lead_agent_id": t.lead_agent_id,
            "policies": t.policies, "agent_ids": agent_ids, "user_ids": user_ids, "project_ids": sorted(projects),
            "created_at": t.created_at, "updated_at": t.updated_at}


class TeamIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    lead_agent_id: int | None = None
    agent_ids: list[int] = Field(default_factory=list, max_length=500)
    user_ids: list[int] = Field(default_factory=list, max_length=500)
    policies: dict[str, Any] = Field(default_factory=dict)


class TeamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    lead_agent_id: int | None = None
    agent_ids: list[int] | None = Field(default=None, max_length=500)
    user_ids: list[int] | None = Field(default=None, max_length=500)
    policies: dict[str, Any] | None = None


def _check_members(db, user: User, agent_ids: list[int], user_ids: list[int], lead: int | None) -> None:
    for aid in {*agent_ids, *([lead] if lead else [])}:
        get_scoped(db, Agent, aid, user)
    for uid in set(user_ids):
        get_scoped(db, User, uid, user)
    if lead is not None and lead not in agent_ids:
        raise HTTPException(422, "lead_must_be_a_member")


def _set_members(db, team_id: int, agent_ids: list[int] | None, user_ids: list[int] | None) -> None:
    if agent_ids is not None:
        db.execute(delete(team_agents).where(team_agents.c.team_id == team_id))
        if agent_ids:
            db.execute(insert(team_agents), [{"team_id": team_id, "agent_id": a} for a in sorted(set(agent_ids))])
    if user_ids is not None:
        db.execute(delete(team_users).where(team_users.c.team_id == team_id))
        if user_ids:
            db.execute(insert(team_users), [{"team_id": team_id, "user_id": u} for u in sorted(set(user_ids))])


@router.get("")
def list_teams(db: DB, user: User = Depends(require("teams.read"))) -> list[dict]:
    rows = db.scalars(select(Team).where(Team.organization_id == user.organization_id).order_by(Team.name))
    return [team_out(db, t) for t in rows]


@router.get("/{team_id}")
def get_team(team_id: int, db: DB, user: User = Depends(require("teams.read"))) -> dict:
    return team_out(db, get_scoped(db, Team, team_id, user))


@router.post("", status_code=201)
def create_team(body: TeamIn, db: DB, user: User = Depends(require("teams.write"))) -> dict:
    if db.scalar(select(Team.id).where(Team.organization_id == user.organization_id, Team.name == body.name)):
        raise HTTPException(409, "name_taken")
    _check_members(db, user, body.agent_ids, body.user_ids, body.lead_agent_id)
    t = Team(organization_id=user.organization_id, name=body.name, description=body.description,
             lead_agent_id=body.lead_agent_id, policies=body.policies)
    db.add(t)
    db.flush()
    _set_members(db, t.id, body.agent_ids, body.user_ids)
    log_activity(db, "teams.created", category="teams", organization_id=user.organization_id, user_id=user.id,
                 details={"team_id": t.id, "agents": body.agent_ids, "users": body.user_ids})
    db.commit()
    return team_out(db, t)


@router.patch("/{team_id}")
def update_team(team_id: int, body: TeamPatch, db: DB, user: User = Depends(require("teams.write"))) -> dict:
    t = get_scoped(db, Team, team_id, user)
    agent_ids, user_ids = _members(db, t.id)
    new_agents = body.agent_ids if body.agent_ids is not None else agent_ids
    lead = body.lead_agent_id if "lead_agent_id" in body.model_fields_set else t.lead_agent_id
    if lead is not None and lead not in new_agents:
        lead = None if "lead_agent_id" not in body.model_fields_set else lead  # a removed lead is cleared
    _check_members(db, user, new_agents, body.user_ids if body.user_ids is not None else user_ids, lead)
    if body.name is not None and body.name != t.name:
        if db.scalar(select(Team.id).where(Team.organization_id == user.organization_id, Team.name == body.name)):
            raise HTTPException(409, "name_taken")
        t.name = body.name
    if body.description is not None:
        t.description = body.description
    if body.policies is not None:
        t.policies = body.policies
    t.lead_agent_id = lead
    _set_members(db, t.id, body.agent_ids, body.user_ids)
    log_activity(db, "teams.updated", category="teams", organization_id=user.organization_id, user_id=user.id,
                 details={"team_id": t.id, **body.model_dump(exclude_unset=True)})
    db.commit()
    return team_out(db, t)


@router.delete("/{team_id}", status_code=204)
def delete_team(team_id: int, db: DB, user: User = Depends(require("teams.write"))) -> None:
    t = get_scoped(db, Team, team_id, user)
    log_activity(db, "teams.deleted", category="teams", organization_id=user.organization_id, user_id=user.id,
                 details={"team_id": t.id, "name": t.name})
    db.delete(t)  # memberships cascade; projects keep running without a team (FK SET NULL)
    db.commit()
