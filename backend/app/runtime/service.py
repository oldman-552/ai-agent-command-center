"""Runtime service: owns execution lifecycle around any ExecutionEngine (spec Phase 2, 15).

Responsibilities: state machine with recorded events, deadline/timeout, cooperative cancellation,
checkpoint persistence + resume, tool permission gate (default deny, audited), retry with exponential
backoff and a retry budget, usage telemetry, agent-state sync.

Note: in this milestone executions run in-process (API background task). M10 moves dispatch to RabbitMQ
workers; this service is unchanged by that — workers call `run_execution`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.control.lifecycle import transition as agent_transition
from app.db import session_factory
from app.models import Agent, AgentState, Checkpoint, Execution, ExecutionEvent
from app.models import ExecutionStatus as X
from app.runtime.contract import (
    TERMINAL,
    TRANSITIONS,
    EngineContext,
    RetryableError,
    ToolDenied,
    get_engine,
    registered_engines,
)
from app.security.audit import log_activity
from app.security.crypto import redact, redact_obj

log = logging.getLogger("runtime")
RUNTIME_ID = f"{socket.gethostname()}:{os.getpid()}"
MAX_BACKOFF_SECONDS = 300

# Tool implementations: name -> async fn(execution, args) -> result. Real tools (files, git, sandbox shell)
# are registered by their planes (M8, M13). A permitted but unregistered tool returns tool_not_available.
ToolFn = Callable[[Execution, dict[str, Any]], Awaitable[dict[str, Any]]]
_TOOLS: dict[str, ToolFn] = {}


def register_tool(name: str, fn: ToolFn) -> None:
    _TOOLS[name] = fn


# ---------------- creation ----------------
def create_execution(db: Session, *, organization_id: int, agent: Agent, input: dict[str, Any], actor: str,
                     task_id: int | None = None, project_id: int | None = None,
                     idempotency_key: str | None = None) -> Execution:
    if idempotency_key:
        existing = db.scalar(select(Execution).where(Execution.idempotency_key == idempotency_key))
        if existing:
            if existing.organization_id != organization_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_key_conflict")
            return existing  # duplicate request → same execution, never a second run
    if agent.archived_at or not agent.is_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "agent_unavailable")
    if agent.execution_engine not in registered_engines():
        raise HTTPException(status.HTTP_409_CONFLICT, f"engine_not_available:{agent.execution_engine}")
    active = db.scalar(select(func.count()).select_from(Execution).where(
        Execution.agent_id == agent.id, Execution.status.not_in([s.value for s in TERMINAL | {X.FAILED}])))
    if active >= agent.concurrency_limit:
        raise HTTPException(status.HTTP_409_CONFLICT, "agent_concurrency_limit")
    engine = get_engine(agent.execution_engine)
    ex = Execution(organization_id=organization_id, agent_id=agent.id, task_id=task_id, project_id=project_id,
                   provider=None, model=agent.model, engine=agent.execution_engine,
                   input_context=redact_obj(input), tool_permissions=list(agent.tool_permissions),
                   idempotency_key=idempotency_key, is_mock=engine.is_mock)
    db.add(ex)
    db.flush()
    _event(db, ex, "state", {"to": X.CREATED.value, "reason": f"created by {actor}"})
    log_activity(db, "executions.created", category="runtime", organization_id=organization_id, actor=actor,
                 agent_id=agent.id, task_id=task_id, execution_id=ex.id)
    return ex


# ---------------- helpers ----------------
def _next_seq(db: Session, ex: Execution) -> int:
    return (db.scalar(select(func.max(ExecutionEvent.seq)).where(ExecutionEvent.execution_id == ex.id)) or 0) + 1


def _event(db: Session, ex: Execution, type_: str, payload: dict[str, Any]) -> None:
    db.add(ExecutionEvent(execution_id=ex.id, seq=_next_seq(db, ex), type=type_, payload=redact_obj(payload)))
    db.flush()


def set_status(db: Session, ex: Execution, to: X, reason: str = "") -> None:
    cur = X(ex.status)
    if to == cur:
        return
    if to not in TRANSITIONS[cur]:
        raise RuntimeError(f"illegal execution transition {cur.value} -> {to.value}")
    ex.status = to.value
    _event(db, ex, "state", {"from": cur.value, "to": to.value, "reason": reason})
    db.commit()


def _agent_to(db: Session, agent: Agent | None, *path: AgentState, reason: str) -> None:
    """Best-effort agent lifecycle sync; with concurrency > 1 the agent may already be busy."""
    if not agent:
        return
    for st in path:
        try:
            agent_transition(db, agent, st, actor="runtime", reason=reason)
        except HTTPException:
            return


def request_cancel(db: Session, ex: Execution, actor: str) -> None:
    if X(ex.status) in TERMINAL:
        return
    ex.cancel_requested = True
    # not currently running anywhere → cancel immediately; running ones observe the flag cooperatively
    idle = (X.CREATED, X.FAILED, X.TERMINATED, X.CHECKPOINTED)
    if X(ex.status) in idle and X.CANCELLED in TRANSITIONS[X(ex.status)]:
        set_status(db, ex, X.CANCELLED, f"cancelled by {actor}")
        ex.ended_at = datetime.now(UTC)
    db.commit()


# ---------------- execution ----------------
async def run_execution(execution_id: str) -> str:
    """Run (or resume) an execution to a terminal/failed state. Safe to call again after a crash."""
    db = session_factory()()
    try:
        return await _run(db, execution_id)
    finally:
        db.close()


async def _run(db: Session, execution_id: str) -> str:
    ex = db.get(Execution, execution_id)
    if ex is None:
        raise KeyError(execution_id)
    if X(ex.status) in TERMINAL or X(ex.status) == X.RUNNING and ex.runtime_id not in (None, RUNTIME_ID):
        return ex.status  # already done, or owned by another live runtime (duplicate dispatch)
    agent = db.get(Agent, ex.agent_id) if ex.agent_id else None
    policy = (agent.retry_policy if agent else None) or {"max_attempts": 1, "backoff_seconds": 0,
                                                         "backoff_multiplier": 1}
    timeout_s = agent.timeout_seconds if agent else 900
    engine = get_engine(ex.engine)
    ex.runtime_id = RUNTIME_ID

    resuming = X(ex.status) in (X.FAILED, X.TERMINATED, X.CHECKPOINTED)
    if resuming:
        set_status(db, ex, X.RECOVERING, "resuming from latest checkpoint")
    else:
        set_status(db, ex, X.INITIALIZING, "engine starting")
        set_status(db, ex, X.READY, "context assembled")
    if ex.deadline_at is None:
        ex.deadline_at = datetime.now(UTC) + timedelta(seconds=timeout_s)
        ex.started_at = datetime.now(UTC)
    _agent_to(db, agent, AgentState.ASSIGNED, AgentState.RUNNING, reason=f"execution {ex.id}")
    db.commit()

    output_parts: list[str] = list((ex.output or {}).get("parts", []))
    usage = {"input_tokens": ex.input_tokens or 0, "output_tokens": ex.output_tokens or 0,
             "cached_tokens": ex.cached_tokens or 0}

    async def is_cancelled() -> bool:
        db.refresh(ex, ["cancel_requested"])
        return bool(ex.cancel_requested)

    async def checkpoint(state: dict[str, Any]) -> None:
        seq = (ex.latest_checkpoint_seq or 0) + 1
        db.add(Checkpoint(execution_id=ex.id, seq=seq, state=redact_obj(state | {"parts": output_parts}),
                          verified=True))
        ex.latest_checkpoint_seq = seq
        ex.output = {"parts": output_parts, "text": "".join(output_parts)}
        _event(db, ex, "checkpoint", {"seq": seq})
        db.commit()

    async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in ex.tool_permissions:
            log_activity(db, "runtime.tool_denied", category="security", level="warning",
                         organization_id=ex.organization_id, actor=f"agent:{ex.agent_id}", agent_id=ex.agent_id,
                         execution_id=ex.id, details={"tool": name})
            _event(db, ex, "tool_denied", {"tool": name})
            db.commit()
            raise ToolDenied(name)
        set_status(db, ex, X.WAITING_FOR_TOOL, f"tool {name}")
        _event(db, ex, "tool_call", {"tool": name, "args": args})
        fn = _TOOLS.get(name)
        result = await fn(ex, args) if fn else {"error": "tool_not_available"}
        _event(db, ex, "tool_result", {"tool": name, "result": result})
        set_status(db, ex, X.RUNNING, "tool returned")
        return result

    max_attempts = int(policy.get("max_attempts", 1))
    delay = float(policy.get("backoff_seconds", 0))
    while True:
        latest = db.scalar(select(Checkpoint).where(Checkpoint.execution_id == ex.id)
                           .order_by(Checkpoint.seq.desc()).limit(1))
        if latest:
            output_parts = list(latest.state.get("parts", output_parts))
        ctx = EngineContext(execution_id=ex.id, agent=_agent_snapshot(agent), input=ex.input_context,
                            tool_permissions=list(ex.tool_permissions),
                            resume_state=latest.state if latest else None,
                            checkpoint=checkpoint, call_tool=call_tool, is_cancelled=is_cancelled)
        set_status(db, ex, X.RUNNING, f"attempt {ex.attempt}")
        remaining = (ex.deadline_at - datetime.now(UTC)).total_seconds()
        try:
            async with asyncio.timeout(max(remaining, 0.01)):
                async for ev in engine.run(ctx):
                    if await is_cancelled():
                        raise asyncio.CancelledError
                    if ev.type == "output_delta":
                        output_parts.append(str(ev.payload.get("text", "")))
                    elif ev.type == "usage":
                        for k in usage:
                            usage[k] += int(ev.payload.get(k, 0) or 0)
                    _event(db, ex, ev.type, ev.payload)
                    db.commit()
            ex.output = {"parts": output_parts, "text": "".join(output_parts)}
            _apply_usage(ex, usage)
            set_status(db, ex, X.COMPLETED, "engine finished")
            ex.ended_at = datetime.now(UTC)
            _agent_to(db, agent, AgentState.COMPLETED, AgentState.IDLE, reason=f"execution {ex.id} completed")
            db.commit()
            return ex.status
        except (asyncio.CancelledError, ToolDenied, RetryableError, TimeoutError, Exception) as e:  # noqa: BLE001
            _apply_usage(ex, usage)
            if isinstance(e, asyncio.CancelledError):
                set_status(db, ex, X.CANCELLED, "cancel requested")
                ex.ended_at = datetime.now(UTC)
                _agent_to(db, agent, AgentState.CANCELLED, AgentState.IDLE, reason="execution cancelled")
                db.commit()
                return ex.status
            category = ("policy" if isinstance(e, ToolDenied) else "timeout" if isinstance(e, TimeoutError)
                        else "transient" if isinstance(e, RetryableError) else "error")
            ex.error, ex.error_category = redact(f"{type(e).__name__}: {e}")[:2000], category
            if X(ex.status) == X.WAITING_FOR_TOOL:
                set_status(db, ex, X.RUNNING, "tool step aborted")
            set_status(db, ex, X.FAILED, category)
            retryable = category == "transient" and ex.attempt < max_attempts
            if retryable:
                wait = min(delay, MAX_BACKOFF_SECONDS)
                _event(db, ex, "retry", {"attempt": ex.attempt + 1, "after_seconds": wait})
                db.commit()
                await asyncio.sleep(wait)
                delay *= float(policy.get("backoff_multiplier", 2))
                ex.attempt += 1
                set_status(db, ex, X.RECOVERING, "retrying after transient failure")
                continue
            ex.ended_at = datetime.now(UTC)
            if agent:
                agent.last_error = ex.error
            _agent_to(db, agent, AgentState.FAILED, AgentState.IDLE, reason=f"execution {ex.id} failed: {category}")
            log_activity(db, "executions.failed", category="runtime", level="error",
                         organization_id=ex.organization_id, agent_id=ex.agent_id, execution_id=ex.id,
                         details={"category": category, "attempts": ex.attempt})
            db.commit()
            return ex.status


def _apply_usage(ex: Execution, usage: dict[str, int]) -> None:
    ex.input_tokens, ex.output_tokens, ex.cached_tokens = (usage["input_tokens"], usage["output_tokens"],
                                                           usage["cached_tokens"])


def _agent_snapshot(agent: Agent | None) -> dict[str, Any]:
    if not agent:
        return {}
    return {"id": agent.id, "name": agent.name, "role": agent.role, "model": agent.model,
            "system_instructions": agent.system_instructions, "context_policy": agent.context_policy}


def mark_orphans_terminated(db: Session, runtime_id: str) -> int:
    """On worker start: executions left RUNNING by a dead instance of this runtime become TERMINATED
    (resumable from their last checkpoint)."""
    rows = db.scalars(select(Execution).where(Execution.runtime_id == runtime_id, Execution.status.in_(
        [X.RUNNING.value, X.WAITING_FOR_TOOL.value, X.CHECKPOINTED.value]))).all()
    for ex in rows:
        set_status(db, ex, X.TERMINATED, "runtime restarted")
    return len(rows)
