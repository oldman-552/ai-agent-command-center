"""Executions API + live event stream (spec Phase 2; UX-02 realtime)."""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.bus.contract import execution_run
from app.bus.outbox import enqueue
from app.config import get_settings
from app.db import session_factory
from app.models import Agent, Execution, ExecutionEvent, User
from app.models import ExecutionStatus as X
from app.runtime import service
from app.runtime.contract import TERMINAL
from app.security.auth import COOKIE_NAME, DB, require
from app.security.crypto import decode_access_token
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api", tags=["executions"])


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: int
    input: dict[str, Any] = Field(default_factory=dict)
    task_id: int | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)


def _dispatch(db, bg: BackgroundTasks, ex: Execution, actor: str, reason: str) -> None:
    """Hand the execution to a worker: an outbox message committed with the caller's transaction (MQ-02)."""
    if get_settings().execution_dispatch == "inline":
        bg.add_task(service.run_execution, ex.id)
        return
    enqueue(db, execution_run(execution_id=ex.id, organization_id=ex.organization_id, engine=ex.engine,
                              sender=actor, reason=reason, project_id=ex.project_id, task_id=ex.task_id))


def execution_out(ex: Execution) -> dict:
    cols = ["id", "agent_id", "project_id", "task_id", "runtime_id", "provider", "model", "engine", "status",
            "started_at", "ended_at", "deadline_at", "output", "input_tokens", "output_tokens", "cached_tokens",
            "cost_usd", "error", "error_category", "evidence", "attempt", "latest_checkpoint_seq", "is_mock",
            "tool_permissions", "created_at", "updated_at"]
    out = {c: getattr(ex, c) for c in cols}
    out["cost_usd"] = str(ex.cost_usd) if ex.cost_usd is not None else None
    return out


@router.post("/executions", status_code=201)
def create(body: ExecutionCreate, bg: BackgroundTasks, db: DB,
           user: User = Depends(require("executions.control"))) -> dict:
    agent = get_scoped(db, Agent, body.agent_id, user)
    ex = service.create_execution(db, organization_id=user.organization_id, agent=agent, input=body.input,
                                  actor=f"user:{user.id}", task_id=body.task_id,
                                  idempotency_key=body.idempotency_key)
    if ex.status == X.CREATED.value:
        _dispatch(db, bg, ex, f"user:{user.id}", "create")
    db.commit()
    return execution_out(ex)


@router.get("/executions")
def list_executions(db: DB, user: User = Depends(require("executions.read")), agent_id: int | None = None,
                    status_: str | None = Query(default=None, alias="status"),
                    limit: int = Query(default=50, le=200)) -> list[dict]:
    stmt = select(Execution).where(Execution.organization_id == user.organization_id)
    if agent_id:
        stmt = stmt.where(Execution.agent_id == agent_id)
    if status_:
        stmt = stmt.where(Execution.status == status_)
    return [execution_out(e) for e in db.scalars(stmt.order_by(Execution.created_at.desc()).limit(limit))]


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str, db: DB, user: User = Depends(require("executions.read"))) -> dict:
    return execution_out(get_scoped(db, Execution, execution_id, user))


@router.get("/executions/{execution_id}/events")
def events(execution_id: str, db: DB, user: User = Depends(require("executions.read")),
           after: int = 0, limit: int = Query(default=500, le=2000)) -> list[dict]:
    get_scoped(db, Execution, execution_id, user)
    rows = db.scalars(select(ExecutionEvent).where(ExecutionEvent.execution_id == execution_id,
                                                   ExecutionEvent.seq > after)
                      .order_by(ExecutionEvent.seq).limit(limit))
    return [{"seq": e.seq, "ts": e.ts, "type": e.type, "payload": e.payload} for e in rows]


@router.post("/executions/{execution_id}/cancel")
def cancel(execution_id: str, db: DB, user: User = Depends(require("executions.control"))) -> dict:
    ex = get_scoped(db, Execution, execution_id, user)
    service.request_cancel(db, ex, f"user:{user.id}")
    return execution_out(ex)


@router.post("/executions/{execution_id}/resume")
def resume(execution_id: str, bg: BackgroundTasks, db: DB,
           user: User = Depends(require("executions.control"))) -> dict:
    ex = get_scoped(db, Execution, execution_id, user)
    if X(ex.status) not in (X.FAILED, X.TERMINATED, X.CHECKPOINTED):
        raise HTTPException(status.HTTP_409_CONFLICT, f"not_resumable:{ex.status}")
    ex.cancel_requested = False
    _dispatch(db, bg, ex, f"user:{user.id}", f"resume:{uuid.uuid4()}")
    db.commit()
    return execution_out(ex)


@router.websocket("/executions/{execution_id}/stream")
async def stream(ws: WebSocket, execution_id: str) -> None:
    """Pushes events in order until the execution ends. Auth: session cookie; same-origin only."""
    origin, host = ws.headers.get("origin"), ws.headers.get("host")
    data = decode_access_token(ws.cookies.get(COOKIE_NAME, ""))
    db = session_factory()()
    try:
        user = db.get(User, int(data["sub"])) if data else None
        ok = (user and user.is_active and user.token_version == data.get("tv")
              and "executions.read" in {p.key for p in user.role.permissions}
              and (origin is None or origin.split("://", 1)[-1] == host))
        ex = db.get(Execution, execution_id) if ok else None
        if not ok or not ex or ex.organization_id != user.organization_id:
            await ws.close(code=4403)
            return
        await ws.accept()
        last = int(ws.query_params.get("after", 0))
        while True:
            rows = db.scalars(select(ExecutionEvent).where(ExecutionEvent.execution_id == execution_id,
                                                           ExecutionEvent.seq > last).order_by(ExecutionEvent.seq))
            for e in rows:
                await ws.send_json({"seq": e.seq, "ts": e.ts.isoformat(), "type": e.type, "payload": e.payload})
                last = e.seq
            db.expire_all()
            ex = db.get(Execution, execution_id)
            if X(ex.status) in TERMINAL | {X.FAILED}:
                await ws.send_json({"type": "end", "status": ex.status})
                await ws.close()
                return
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        return
    finally:
        db.close()
