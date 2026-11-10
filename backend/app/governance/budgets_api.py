"""Budgets API (GW-08) and the routing preview (GW-07)."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.governance import budgets
from app.models import Agent, Budget, Project, Provider, Task, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/budgets", tags=["budgets"])
SCOPE_MODEL = {"agent": Agent, "project": Project, "task": Task, "provider": Provider}


class BudgetIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "agent", "project", "task", "provider"]
    scope_id: int | None = None
    period: Literal["daily", "monthly", "total"]
    limit_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    limit_tokens: int | None = Field(default=None, gt=0)
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self):
        if self.limit_usd is None and self.limit_tokens is None:
            raise ValueError("set limit_usd and/or limit_tokens")
        if (self.scope == "global") != (self.scope_id is None):
            raise ValueError("scope_id is required except for global budgets")
        return self


class BudgetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit_usd: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    limit_tokens: int | None = Field(default=None, gt=0)
    enabled: bool | None = None


def out(db, b: Budget) -> dict:
    return {"id": b.id, "enabled": b.enabled, **budgets.status_of(db, b).as_dict()}


@router.get("")
def list_budgets(db: DB, user: User = Depends(require("providers.read"))) -> list[dict]:
    rows = db.scalars(select(Budget).where(Budget.organization_id == user.organization_id).order_by(Budget.id))
    return [out(db, b) for b in rows]


@router.post("", status_code=201)
def create_budget(body: BudgetIn, db: DB, user: User = Depends(require("budgets.manage"))) -> dict:
    if body.scope != "global":
        get_scoped(db, SCOPE_MODEL[body.scope], body.scope_id, user)
    b = Budget(organization_id=user.organization_id, **body.model_dump())
    db.add(b)
    db.flush()
    log_activity(db, "budget.created", category="budget", organization_id=user.organization_id, user_id=user.id,
                 details={"budget_id": b.id, **{k: str(v) for k, v in body.model_dump().items()}})
    db.commit()
    return out(db, b)


@router.patch("/{budget_id}")
def update_budget(budget_id: int, body: BudgetPatch, db: DB, user: User = Depends(require("budgets.manage"))) -> dict:
    b = get_scoped(db, Budget, budget_id, user)
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(b, k, v)
    if b.limit_usd is None and b.limit_tokens is None:
        raise HTTPException(422, "a budget needs a limit")
    log_activity(db, "budget.updated", category="budget", organization_id=user.organization_id, user_id=user.id,
                 details={"budget_id": b.id, **{k: str(v) for k, v in data.items()}})
    db.commit()
    return out(db, b)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, db: DB, user: User = Depends(require("budgets.manage"))) -> None:
    b = get_scoped(db, Budget, budget_id, user)
    log_activity(db, "budget.deleted", category="budget", organization_id=user.organization_id, user_id=user.id,
                 details={"budget_id": b.id})
    db.delete(b)
    db.commit()


routing_router = APIRouter(prefix="/api/routing", tags=["routing"])


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: int
    prompt: str = Field(default="", max_length=200_000)
    task_type: str | None = Field(default=None, max_length=32)
    complexity: Literal["low", "medium", "high"] | None = None


@routing_router.post("/preview")
def preview(body: PreviewIn, db: DB, user: User = Depends(require("providers.read"))) -> dict:
    """Which model would an auto-routed agent use for this request, and why."""
    from app.connectivity.gateway import routing
    agent = get_scoped(db, Agent, body.agent_id, user)
    need = routing.assess(agent, {"prompt": body.prompt, "complexity": body.complexity}, body.task_type)
    ranked, rejected = routing.rank(db, agent, need)
    return routing.explain(need, ranked, rejected)
