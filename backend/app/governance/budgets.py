"""Budgets (GW-08, spec Phase 20, ADR-027).

A budget limits spend (USD from recorded costs) and/or tokens (from usage records) for a scope — organization
(`global`), `agent`, `project`, `task` or `provider` — over a period (`daily`, `monthly`, `total`). A task's own
`budget_usd` and a project's `budget_usd` act as implicit total budgets.

When a budget is exhausted:
- new executions in its scope are refused (`budget_exceeded`); running ones finish, completed results are kept;
- the event is audited once per period and an approval request (`budget.increase`) is opened, deduplicated;
- everything outside the scope keeps running (the orchestrator just waits for that task);
- an approved increase raises the limit by exactly the approved amount (frozen payload).
Only real recorded usage counts — mock providers record no cost, and nothing is estimated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Approval, Budget, CostRecord, Project, Task, UsageRecord
from app.security.audit import log_activity

SCOPES = ("global", "agent", "project", "task", "provider")
PERIODS = ("daily", "monthly", "total")


def window_start(period: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(UTC)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


@dataclass
class Status:
    budget_id: int | None
    scope: str
    scope_id: int | None
    period: str
    limit_usd: Decimal | None
    limit_tokens: int | None
    spent_usd: Decimal
    spent_tokens: int

    @property
    def exceeded(self) -> bool:
        return (self.limit_usd is not None and self.spent_usd >= self.limit_usd) or \
               (self.limit_tokens is not None and self.spent_tokens >= self.limit_tokens)

    def as_dict(self) -> dict[str, Any]:
        return {"budget_id": self.budget_id, "scope": self.scope, "scope_id": self.scope_id, "period": self.period,
                "limit_usd": str(self.limit_usd) if self.limit_usd is not None else None,
                "limit_tokens": self.limit_tokens, "spent_usd": str(self.spent_usd), "spent_tokens": self.spent_tokens,
                "exceeded": self.exceeded}


def _filters(model, organization_id: int, scope: str, scope_id: int | None, since: datetime | None):
    conds = [model.organization_id == organization_id]
    if since is not None:
        conds.append(model.ts >= since)
    col = {"agent": "agent_id", "project": "project_id", "task": "task_id"}.get(scope)
    if col:
        conds.append(getattr(model, col) == scope_id)
    return conds


def spent(db: Session, organization_id: int, scope: str, scope_id: int | None, period: str) -> tuple[Decimal, int]:
    since = window_start(period)
    tok_stmt = select(func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0)).where(
        *_filters(UsageRecord, organization_id, scope, scope_id, since), UsageRecord.is_mock.is_(False))
    usd_stmt = select(func.coalesce(func.sum(CostRecord.amount_usd), 0))
    if scope == "provider":
        tok_stmt = tok_stmt.where(UsageRecord.provider_id == scope_id)
        usd_stmt = usd_stmt.join(UsageRecord, UsageRecord.id == CostRecord.usage_record_id).where(
            *_filters(CostRecord, organization_id, "global", None, since), UsageRecord.provider_id == scope_id)
    else:
        usd_stmt = usd_stmt.where(*_filters(CostRecord, organization_id, scope, scope_id, since))
    return Decimal(db.scalar(usd_stmt) or 0), int(db.scalar(tok_stmt) or 0)


def status_of(db: Session, b: Budget) -> Status:
    usd, tok = spent(db, b.organization_id, b.scope, b.scope_id, b.period)
    return Status(b.id, b.scope, b.scope_id, b.period, b.limit_usd, b.limit_tokens, usd, tok)


def applicable(db: Session, organization_id: int, *, agent_id: int | None = None, project_id: int | None = None,
               task_id: int | None = None, provider_id: int | None = None) -> list[Status]:
    keys = [("global", None), ("agent", agent_id), ("project", project_id), ("task", task_id),
            ("provider", provider_id)]
    out: list[Status] = []
    for b in db.scalars(select(Budget).where(Budget.organization_id == organization_id, Budget.enabled.is_(True))):
        if (b.scope, b.scope_id if b.scope != "global" else None) in keys and (b.scope == "global" or b.scope_id):
            out.append(status_of(db, b))
    if task_id:  # implicit budgets carried by the work items themselves
        t = db.get(Task, task_id)
        if t is not None and t.budget_usd is not None:
            usd, tok = spent(db, organization_id, "task", task_id, "total")
            out.append(Status(None, "task", task_id, "total", t.budget_usd, None, usd, tok))
    if project_id:
        p = db.get(Project, project_id)
        if p is not None and p.budget_usd is not None:
            usd, tok = spent(db, organization_id, "project", project_id, "total")
            out.append(Status(None, "project", project_id, "total", p.budget_usd, None, usd, tok))
    return out


def exceeded(db: Session, organization_id: int, **scope: int | None) -> list[Status]:
    return [s for s in applicable(db, organization_id, **scope) if s.exceeded]


def on_exceeded(db: Session, organization_id: int, statuses: list[Status], actor: str, **ctx: Any) -> list[int]:
    """Audit the stop and open (or reuse) one approval request per exhausted budget. Does not commit."""
    from app.governance.approvals import request_approval
    ids = []
    period_key = datetime.now(UTC).strftime("%Y-%m-%d")
    for s in statuses:
        ref = f"budget:{s.budget_id}" if s.budget_id else f"{s.scope}:{s.scope_id}:implicit"
        increase = (s.limit_usd * Decimal("0.5")).quantize(Decimal("0.0001")) if s.limit_usd else None
        increase_tokens = int(s.limit_tokens * 0.5) if s.limit_tokens else None
        a = request_approval(
            db, organization_id=organization_id, operation="budget.increase", requested_by=actor,
            title=f"Budget exhausted: {s.scope}{f' #{s.scope_id}' if s.scope_id else ''} ({s.period})",
            summary=(f"Spent {s.spent_usd} USD / {s.spent_tokens} tokens of {s.limit_usd} USD / {s.limit_tokens} "
                     "tokens. New work in this scope is paused until the limit is raised."),
            payload={"budget_id": s.budget_id, "scope": s.scope, "scope_id": s.scope_id, "period": s.period,
                     "increase_usd": str(increase) if increase is not None else None,
                     "increase_tokens": increase_tokens},
            idempotency_key=f"budget.increase:{ref}:{period_key}:{s.limit_usd}:{s.limit_tokens}",
            agent_id=ctx.get("agent_id"), project_id=ctx.get("project_id"), task_id=ctx.get("task_id"))
        ids.append(a.id)
        log_activity(db, "budget.exceeded", category="budget", level="warning", organization_id=organization_id,
                     actor=actor, agent_id=ctx.get("agent_id"), project_id=ctx.get("project_id"),
                     task_id=ctx.get("task_id"), details={**s.as_dict(), "approval_id": a.id})
    return ids


def guard(db: Session, organization_id: int, actor: str, *, agent_id: int | None, project_id: int | None,
          task_id: int | None) -> None:
    """Called before any new execution. Raises HTTP 402 `budget_exceeded` when a budget in scope is exhausted."""
    from fastapi import HTTPException
    hit = exceeded(db, organization_id, agent_id=agent_id, project_id=project_id, task_id=task_id)
    if hit:
        # recorded in its own transaction: the caller's transaction (an API request or a scheduling round holding
        # row locks) is not committed early, and the stop is recorded even though the caller fails/rolls back
        from app.db import session_factory
        with session_factory()() as side:
            on_exceeded(side, organization_id, hit, actor, agent_id=agent_id, project_id=project_id, task_id=task_id)
            side.commit()
        raise HTTPException(402, "budget_exceeded")


def _increase(db: Session, a: Approval) -> dict[str, Any]:
    p = a.payload
    inc_usd = Decimal(p["increase_usd"]) if p.get("increase_usd") else None
    inc_tok = int(p["increase_tokens"]) if p.get("increase_tokens") else None
    if p.get("budget_id"):
        b = db.get(Budget, int(p["budget_id"]), with_for_update=True)
        if b is None or b.organization_id != a.organization_id:
            raise ValueError("budget missing")
        if inc_usd is not None and b.limit_usd is not None:
            b.limit_usd = b.limit_usd + inc_usd
        if inc_tok is not None and b.limit_tokens is not None:
            b.limit_tokens = b.limit_tokens + inc_tok
        return {"budget_id": b.id, "limit_usd": str(b.limit_usd) if b.limit_usd is not None else None,
                "limit_tokens": b.limit_tokens}
    model = Task if p["scope"] == "task" else Project
    row = db.get(model, int(p["scope_id"]))
    row.budget_usd = (row.budget_usd or Decimal(0)) + (inc_usd or Decimal(0))
    return {p["scope"]: row.id, "budget_usd": str(row.budget_usd)}


def _register() -> None:
    from app.governance.approvals import register_executor
    register_executor("budget.increase", _increase)


_register()
