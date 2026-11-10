"""Audit log search (SEC-06). Read-only, tenant-scoped, keyset-paginated; entries were redacted on insert and are
redacted again on the way out (defence in depth). Export is CSV with the same filters."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, func, or_, select

from app.models import AuditLog, User
from app.security.auth import DB, require
from app.security.crypto import redact, redact_obj

router = APIRouter(prefix="/api/audit", tags=["audit"])
LEVELS = ("debug", "info", "warning", "error", "critical")


def entry_out(e: AuditLog) -> dict:
    return {"id": e.id, "ts": e.ts, "actor": e.actor, "action": e.action, "category": e.category, "level": e.level,
            "message": redact(e.message or ""), "details": redact_obj(e.details or {}), "user_id": e.user_id,
            "agent_id": e.agent_id, "project_id": e.project_id, "task_id": e.task_id, "execution_id": e.execution_id,
            "tokens": e.tokens, "cost_usd": str(e.cost_usd) if e.cost_usd is not None else None}


def _filtered(user: User, q: str | None, category: str | None, level: str | None, actor: str | None,
              action: str | None, agent_id: int | None, project_id: int | None, task_id: int | None,
              execution_id: str | None, since: datetime | None, until: datetime | None) -> Select:
    stmt = select(AuditLog).where(AuditLog.organization_id == user.organization_id)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(or_(AuditLog.action.ilike(like), AuditLog.message.ilike(like), AuditLog.actor.ilike(like)))
    if category:
        stmt = stmt.where(AuditLog.category == category)
    if level:
        if level not in LEVELS:
            raise HTTPException(422, "invalid_level")
        stmt = stmt.where(AuditLog.level.in_(LEVELS[LEVELS.index(level):]))  # this level and more severe
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)
    if action:
        stmt = stmt.where(AuditLog.action.startswith(action))
    for col, val in ((AuditLog.agent_id, agent_id), (AuditLog.project_id, project_id), (AuditLog.task_id, task_id),
                     (AuditLog.execution_id, execution_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if since:
        stmt = stmt.where(AuditLog.ts >= since)
    if until:
        stmt = stmt.where(AuditLog.ts < until)
    return stmt


def _params(q: str | None = Query(default=None, max_length=200), category: str | None = None,
            level: str | None = None, actor: str | None = Query(default=None, max_length=64),
            action: str | None = Query(default=None, max_length=64), agent_id: int | None = None,
            project_id: int | None = None, task_id: int | None = None, execution_id: str | None = None,
            since: datetime | None = None, until: datetime | None = None) -> dict:
    return locals()


@router.get("")
def search(db: DB, f: dict = Depends(_params), user: User = Depends(require("audit.read")),
           before_id: int | None = None, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    stmt = _filtered(user, **f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    if before_id:
        stmt = stmt.where(AuditLog.id < before_id)
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    cats = db.scalars(select(AuditLog.category).where(AuditLog.organization_id == user.organization_id)
                      .group_by(AuditLog.category).order_by(AuditLog.category)).all()
    return {"items": [entry_out(e) for e in rows], "total": total,
            "next_before_id": rows[-1].id if more and rows else None, "categories": cats}


@router.get("/export.csv")
def export(db: DB, f: dict = Depends(_params), user: User = Depends(require("audit.read"))) -> StreamingResponse:
    rows = db.scalars(_filtered(user, **f).order_by(AuditLog.id.desc()).limit(10_000)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "ts", "actor", "action", "category", "level", "message", "agent_id", "project_id", "task_id",
                "execution_id"])
    for e in rows:
        cells = [e.id, e.ts.isoformat(), e.actor, e.action, e.category, e.level, redact(e.message or ""), e.agent_id,
                 e.project_id, e.task_id, e.execution_id]
        # neutralise spreadsheet formula injection in text cells
        w.writerow([f"'{c}" if isinstance(c, str) and c[:1] in ("=", "+", "-", "@") else c for c in cells])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=audit.csv"})
