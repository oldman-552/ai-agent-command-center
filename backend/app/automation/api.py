"""Automations API (UX-06): create, edit, pause, resume, run now, delete, history. In-app only."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.automation import engine
from app.models import Automation, AutomationRun, Project, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/automations", tags=["automations"])


def out(a: Automation) -> dict[str, Any]:
    return {"id": a.id, "name": a.name, "project_id": a.project_id, "trigger": a.trigger, "action": a.action,
            "status": a.status, "last_run_at": a.last_run_at, "next_run_at": a.next_run_at,
            "created_by_user_id": a.created_by_user_id, "created_at": a.created_at, "updated_at": a.updated_at}


def run_out(r: AutomationRun) -> dict[str, Any]:
    return {"id": r.id, "trigger": r.trigger, "status": r.status, "detail": r.detail, "result": r.result,
            "started_at": r.started_at, "finished_at": r.finished_at}


class AutomationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    project_id: int
    trigger: dict[str, Any]
    action: dict[str, Any]


class AutomationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    trigger: dict[str, Any] | None = None
    action: dict[str, Any] | None = None


def _check(user: User, trigger: dict, action: dict, project_id: int | None) -> None:
    try:
        engine.validate(trigger, action, project_id)
    except engine.AutomationError as e:
        raise HTTPException(422, str(e)) from e
    need = engine.ACTION_PERMISSION[action["type"]]
    if need not in {p.key for p in user.role.permissions}:
        raise HTTPException(403, f"{need}_required")


@router.get("")
def list_automations(db: DB, user: User = Depends(require("projects.read")), project_id: int | None = None) -> list:
    stmt = select(Automation).where(Automation.organization_id == user.organization_id)
    if project_id:
        stmt = stmt.where(Automation.project_id == project_id)
    return [out(a) for a in db.scalars(stmt.order_by(Automation.id))]


@router.post("", status_code=201)
def create(body: AutomationIn, db: DB, user: User = Depends(require("automations.manage"))) -> dict:
    get_scoped(db, Project, body.project_id, user)
    _check(user, body.trigger, body.action, body.project_id)
    a = Automation(organization_id=user.organization_id, project_id=body.project_id, name=body.name,
                   trigger=body.trigger, action=body.action, status="active", created_by_user_id=user.id, state={},
                   next_run_at=engine.next_run(body.trigger, datetime.now(UTC)))
    db.add(a)
    db.flush()
    log_activity(db, "automation.created", category="automation", organization_id=user.organization_id,
                 user_id=user.id, project_id=a.project_id, details={"automation_id": a.id, "trigger": a.trigger,
                                                                     "action": a.action["type"]})
    db.commit()
    return out(a)


@router.patch("/{aid}")
def edit(aid: int, body: AutomationPatch, db: DB, user: User = Depends(require("automations.manage"))) -> dict:
    a = get_scoped(db, Automation, aid, user)
    trigger, action = body.trigger or a.trigger, body.action or a.action
    _check(user, trigger, action, a.project_id)
    if body.name is not None:
        a.name = body.name
    if body.trigger is not None:
        a.trigger, a.state = trigger, {}
        a.next_run_at = engine.next_run(trigger, datetime.now(UTC))
    a.action = action
    a.created_by_user_id = user.id  # the editor now owns it (their permissions are checked at run time)
    log_activity(db, "automation.updated", category="automation", organization_id=user.organization_id,
                 user_id=user.id, project_id=a.project_id, details={"automation_id": a.id})
    db.commit()
    return out(a)


@router.post("/{aid}/pause")
def pause(aid: int, db: DB, user: User = Depends(require("automations.manage"))) -> dict:
    a = get_scoped(db, Automation, aid, user)
    a.status = "paused"
    log_activity(db, "automation.paused", category="automation", organization_id=user.organization_id,
                 user_id=user.id, project_id=a.project_id, details={"automation_id": a.id})
    db.commit()
    return out(a)


@router.post("/{aid}/resume")
def resume(aid: int, db: DB, user: User = Depends(require("automations.manage"))) -> dict:
    a = get_scoped(db, Automation, aid, user)
    a.status, a.state = "active", {}
    a.next_run_at = engine.next_run(a.trigger, datetime.now(UTC))  # no catch-up storm after a pause
    log_activity(db, "automation.resumed", category="automation", organization_id=user.organization_id,
                 user_id=user.id, project_id=a.project_id, details={"automation_id": a.id})
    db.commit()
    return out(a)


@router.post("/{aid}/run")
def run_now(aid: int, db: DB, user: User = Depends(require("automations.manage"))) -> dict:
    a = get_scoped(db, Automation, aid, user)
    r = engine.run_once(db, a, "manual", f"run by user:{user.id}")
    db.commit()
    return run_out(r)


@router.get("/{aid}/runs")
def runs(aid: int, db: DB, user: User = Depends(require("projects.read")),
         limit: int = Query(default=50, le=200)) -> list[dict]:
    a = get_scoped(db, Automation, aid, user)
    rows = db.scalars(select(AutomationRun).where(AutomationRun.automation_id == a.id)
                      .order_by(AutomationRun.id.desc()).limit(limit))
    return [run_out(r) for r in rows]


@router.delete("/{aid}", status_code=204)
def delete(aid: int, db: DB, user: User = Depends(require("automations.manage"))) -> None:
    a = get_scoped(db, Automation, aid, user)
    log_activity(db, "automation.deleted", category="automation", organization_id=user.organization_id,
                 user_id=user.id, project_id=a.project_id, details={"automation_id": a.id, "name": a.name})
    db.delete(a)
    db.commit()
