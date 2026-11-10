"""A2A server for platform agents (A2A-01, ADR-015). Targets A2A protocol v1.0, JSON-RPC binding.

Each exposed platform agent is an A2A agent:
    GET  /api/a2a/agents/{identifier}/.well-known/agent-card.json      Agent Card
    POST /api/a2a/agents/{identifier}                                   JSON-RPC 2.0 (header A2A-Version: 1.0)

Security: nothing is exposed by default. A caller needs a bearer A2A access key (created by a human with
`security.manage`) that names the agent. Unknown/unlisted agents answer 404 so their existence is not revealed.
An A2A Task *is* a platform Execution: the full runtime (state machine, tool gate, budgets, audit) applies.

Supported: SendMessage (blocking or returnImmediately), GetTask, ListTasks, CancelTask, and streaming —
SendStreamingMessage / SubscribeToTask answer `text/event-stream`; every `data:` line is a JSON-RPC response with
the request's id whose result is a StreamResponse (`task` first, then `statusUpdate` / `artifactUpdate` with
`append`/`lastChunk` chunks of the output); the stream closes when the task is terminal or needs input.
Not yet: push notifications, extended card, multi-turn follow-ups — answered with the spec's errors and advertised
as false in the card.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import session_factory
from app.models import A2AAccessKey, Agent, Execution, ExecutionEvent
from app.models import ExecutionStatus as X
from app.runtime import service
from app.runtime.contract import TERMINAL
from app.runtime.dispatch import dispatch
from app.security.audit import log_activity

log = logging.getLogger("a2a.server")
router = APIRouter(prefix="/api/a2a", tags=["a2a"])

PROTOCOL_VERSION = "1.0"
BLOCKING_WAIT_MAX_S = 120.0
STREAM_MAX_S = 3600.0
STREAM_POLL_S = 0.25
STREAMING = {"SendStreamingMessage", "SubscribeToTask"}

# ---- JSON-RPC / A2A error codes (spec §5.4) ----
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
TASK_NOT_FOUND, TASK_NOT_CANCELABLE, PUSH_NOT_SUPPORTED, UNSUPPORTED_OPERATION = -32001, -32002, -32003, -32004
CONTENT_TYPE_NOT_SUPPORTED, EXTENDED_CARD_NOT_CONFIGURED, VERSION_NOT_SUPPORTED = -32005, -32007, -32009

STATE = {  # execution status → A2A TaskState
    X.CREATED: "TASK_STATE_SUBMITTED", X.INITIALIZING: "TASK_STATE_SUBMITTED", X.READY: "TASK_STATE_SUBMITTED",
    X.RUNNING: "TASK_STATE_WORKING", X.WAITING_FOR_TOOL: "TASK_STATE_WORKING",
    X.WAITING_FOR_APPROVAL: "TASK_STATE_WORKING",  # platform approvals are internal to the platform
    X.CHECKPOINTED: "TASK_STATE_WORKING", X.RECOVERING: "TASK_STATE_WORKING", X.TERMINATED: "TASK_STATE_WORKING",
    X.WAITING_FOR_INPUT: "TASK_STATE_INPUT_REQUIRED",
    X.COMPLETED: "TASK_STATE_COMPLETED", X.FAILED: "TASK_STATE_FAILED", X.CANCELLED: "TASK_STATE_CANCELED",
}
STATE_TO_STATUSES: dict[str, list[str]] = {}
for _x, _s in STATE.items():
    STATE_TO_STATUSES.setdefault(_s, []).append(_x.value)
FINAL = TERMINAL | {X.FAILED, X.WAITING_FOR_INPUT}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


# ---- request models (v1.0 JSON names) ----
class Part(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str | None = None


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    messageId: str = Field(min_length=1, max_length=100)
    role: str
    parts: list[Part] = Field(min_length=1, max_length=50)
    contextId: str | None = Field(default=None, max_length=64)
    taskId: str | None = None


class SendConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    returnImmediately: bool = False


class SendParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: Message
    configuration: SendConfig = Field(default_factory=SendConfig)


class TaskIdParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str


class ListParams(BaseModel):
    model_config = ConfigDict(extra="allow")
    contextId: str | None = None
    status: str | None = None
    pageSize: int = Field(default=50, ge=1, le=100)
    pageToken: str = ""


# ---- auth ----
def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _authorize(db: Session, request: Request, identifier: str) -> tuple[A2AAccessKey, Agent]:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a2a_key_required", headers={"WWW-Authenticate": "Bearer"})
    key = db.scalar(select(A2AAccessKey).where(A2AAccessKey.key_hash == hash_key(token.strip()),
                                               A2AAccessKey.revoked_at.is_(None)))
    if key is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "a2a_key_invalid", headers={"WWW-Authenticate": "Bearer"})
    agent = db.scalar(select(Agent).where(Agent.organization_id == key.organization_id,
                                          Agent.identifier == identifier, Agent.archived_at.is_(None)))
    if agent is None or agent.id not in (key.agent_ids or []):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    key.last_used_at = datetime.now(UTC)
    return key, agent


# ---- representation ----
def agent_card(agent: Agent, base_url: str) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/a2a/agents/{agent.identifier}"
    return {
        "name": agent.name,
        "description": agent.description or f"{agent.role} agent",
        "version": str(agent.config_version),
        "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": PROTOCOL_VERSION}],
        "provider": {"organization": "AI Agent Command Center", "url": base_url.rstrip("/")},
        "capabilities": {"streaming": True, "pushNotifications": False, "extendedAgentCard": False},
        "securitySchemes": {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer",
                                                                  "description": "A2A access key"}}},
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [{"id": agent.role, "name": agent.role, "description": agent.description or agent.name,
                    "tags": [agent.role, *[str(s) for s in (agent.skills or [])][:10]],
                    "inputModes": ["text/plain"], "outputModes": ["text/plain"]}],
    }


def task_json(ex: Execution) -> dict[str, Any]:
    meta = (ex.input_context or {}).get("a2a", {})
    st = X(ex.status)
    status_obj: dict[str, Any] = {"state": STATE[st], "timestamp": (ex.updated_at or ex.created_at).isoformat()}
    if st == X.FAILED:
        status_obj["message"] = {"messageId": f"{ex.id}-status", "role": "ROLE_AGENT", "taskId": ex.id,
                                 "contextId": meta.get("context_id"),
                                 "parts": [{"text": f"failed: {ex.error_category or 'error'}"}]}
    task: dict[str, Any] = {"id": ex.id, "contextId": meta.get("context_id"), "status": status_obj,
                            "metadata": {"isMock": bool(ex.is_mock)}}
    text = (ex.output or {}).get("text")
    if st == X.COMPLETED and text is not None:
        task["artifacts"] = [{"artifactId": f"{ex.id}-output", "name": "output", "parts": [{"text": text}]}]
    return task


# ---- methods ----
def _start(db: Session, key: A2AAccessKey, agent: Agent, params: SendParams, run_inline) -> Execution:
    """Create (or, for a retried messageId, find) the platform execution behind an A2A task and dispatch it."""
    msg = params.message
    if msg.role not in ("ROLE_USER", "user"):
        raise RpcError(INVALID_PARAMS, "message.role must be ROLE_USER")
    if msg.taskId:
        raise RpcError(UNSUPPORTED_OPERATION, "follow-up messages to an existing task are not supported yet")
    if any(p.text is None for p in msg.parts):
        raise RpcError(CONTENT_TYPE_NOT_SUPPORTED, "only text parts are supported")
    text = "\n".join(p.text or "" for p in msg.parts)
    context_id = msg.contextId or str(uuid.uuid4())
    try:
        ex = service.create_execution(
            db, organization_id=key.organization_id, agent=agent, actor=f"a2a:key:{key.id}",
            idempotency_key=f"a2a:{key.id}:{msg.messageId}",
            input={"prompt": text, "a2a": {"key_id": key.id, "context_id": context_id, "message_id": msg.messageId}})
    except HTTPException as e:  # agent unavailable / concurrency limit / engine missing → the agent refuses
        raise RpcError(UNSUPPORTED_OPERATION, f"agent cannot accept the task: {e.detail}") from e
    if (ex.input_context or {}).get("a2a", {}).get("key_id") != key.id:
        raise RpcError(INVALID_PARAMS, "messageId already used")
    if ex.status == X.CREATED.value:
        log_activity(db, "a2a.task_received", category="a2a", organization_id=key.organization_id,
                     actor=f"a2a:key:{key.id}", agent_id=agent.id, execution_id=ex.id)
        dispatch(db, ex, f"a2a:key:{key.id}", "create", run_inline=run_inline)
    db.commit()
    return ex


async def _send(db: Session, key: A2AAccessKey, agent: Agent, params: SendParams, bg: BackgroundTasks) -> dict:
    ex = _start(db, key, agent, params, bg.add_task)
    if not params.configuration.returnImmediately:
        deadline = asyncio.get_running_loop().time() + min(float(agent.timeout_seconds), BLOCKING_WAIT_MAX_S)
        while X(ex.status) not in FINAL and asyncio.get_running_loop().time() < deadline:
            if bg.tasks:  # inline mode: run it here instead of after the response
                for t in list(bg.tasks):
                    await t()
                bg.tasks.clear()
            await asyncio.sleep(0.2)
            db.expire_all()
            ex = db.get(Execution, ex.id)
    return {"task": task_json(ex)}


def _own_task(db: Session, key: A2AAccessKey, agent: Agent, task_id: str) -> Execution:
    ex = db.get(Execution, task_id)
    if (ex is None or ex.organization_id != key.organization_id or ex.agent_id != agent.id
            or (ex.input_context or {}).get("a2a", {}).get("key_id") != key.id):
        raise RpcError(TASK_NOT_FOUND, "task not found")
    return ex


def _list(db: Session, key: A2AAccessKey, agent: Agent, p: ListParams) -> dict:
    stmt = select(Execution).where(Execution.organization_id == key.organization_id, Execution.agent_id == agent.id,
                                   Execution.input_context["a2a"]["key_id"].as_integer() == key.id)
    if p.contextId:
        stmt = stmt.where(Execution.input_context["a2a"]["context_id"].astext == p.contextId)
    if p.status:
        if p.status not in STATE_TO_STATUSES:
            raise RpcError(INVALID_PARAMS, f"unknown status {p.status}")
        stmt = stmt.where(Execution.status.in_(STATE_TO_STATUSES[p.status]))
    try:
        offset = int(p.pageToken or 0)
    except ValueError as e:
        raise RpcError(INVALID_PARAMS, "invalid pageToken") from e
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Execution.created_at.desc(), Execution.id).offset(offset).limit(p.pageSize)).all()
    nxt = offset + len(rows)
    return {"tasks": [task_json(e) for e in rows], "totalSize": total, "pageSize": p.pageSize,
            "nextPageToken": str(nxt) if nxt < total else ""}


def _cancel(db: Session, key: A2AAccessKey, agent: Agent, p: TaskIdParams) -> dict:
    ex = _own_task(db, key, agent, p.id)
    if X(ex.status) in TERMINAL:
        raise RpcError(TASK_NOT_CANCELABLE, f"task is {STATE[X(ex.status)]}")
    service.request_cancel(db, ex, f"a2a:key:{key.id}")
    db.commit()
    db.refresh(ex)
    return task_json(ex)


UNSUPPORTED = {"CreateTaskPushNotificationConfig",
               "GetTaskPushNotificationConfig", "ListTaskPushNotificationConfigs", "DeleteTaskPushNotificationConfig"}


async def _call(db: Session, key: A2AAccessKey, agent: Agent, method: str, params: Any, bg: BackgroundTasks) -> Any:
    try:
        if method == "SendMessage":
            return await _send(db, key, agent, SendParams.model_validate(params or {}), bg)
        if method == "GetTask":
            return task_json(_own_task(db, key, agent, TaskIdParams.model_validate(params or {}).id))
        if method == "ListTasks":
            return _list(db, key, agent, ListParams.model_validate(params or {}))
        if method == "CancelTask":
            return _cancel(db, key, agent, TaskIdParams.model_validate(params or {}))
    except ValidationError as e:
        raise RpcError(INVALID_PARAMS, "invalid params", e.errors(include_url=False, include_input=False)) from e
    if method == "GetExtendedAgentCard":
        raise RpcError(EXTENDED_CARD_NOT_CONFIGURED, "extended agent card not configured")
    if method in UNSUPPORTED:
        code = PUSH_NOT_SUPPORTED if "PushNotification" in method else UNSUPPORTED_OPERATION
        raise RpcError(code, f"{method} is not supported by this agent")
    raise RpcError(METHOD_NOT_FOUND, f"method not found: {method}")


# ---- streaming ----
def _open_stream(db: Session, key: A2AAccessKey, agent: Agent, method: str, params: Any) -> tuple[str, list]:
    """Validate and start; errors are raised here so they are answered as plain JSON-RPC errors, not a stream."""
    inline: list = []
    try:
        if method == "SendStreamingMessage":
            ex = _start(db, key, agent, SendParams.model_validate(params or {}),
                        lambda fn, *a: inline.append((fn, a)))
            return ex.id, inline
        ex = _own_task(db, key, agent, TaskIdParams.model_validate(params or {}).id)
    except ValidationError as e:
        raise RpcError(INVALID_PARAMS, "invalid params", e.errors(include_url=False, include_input=False)) from e
    if X(ex.status) in FINAL:
        raise RpcError(UNSUPPORTED_OPERATION, f"task is {STATE[X(ex.status)]}; nothing to subscribe to")
    return ex.id, inline


async def _events(request: Request, id_: Any, task_id: str, inline: list) -> AsyncIterator[str]:
    """StreamResponse events for one task until it is final. Output deltas become artifact chunks."""
    def frame(result: dict) -> str:
        return f"data: {json.dumps({'jsonrpc': '2.0', 'id': id_, 'result': result}, default=str)}\n\n"

    runners = [asyncio.create_task(fn(*a)) for fn, a in inline]  # inline mode (tests): run it alongside the stream
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STREAM_MAX_S
    last_seq, chunks, last_state = 0, 0, None
    try:
        while True:
            with session_factory()() as db:
                ex = db.get(Execution, task_id)
                if ex is None:
                    return
                task = task_json(ex)
                ctx_id = task["contextId"]
                if last_state is None:
                    yield frame({"task": task})
                    last_state = task["status"]["state"]
                deltas = db.scalars(select(ExecutionEvent).where(
                    ExecutionEvent.execution_id == task_id, ExecutionEvent.type == "output_delta",
                    ExecutionEvent.seq > last_seq).order_by(ExecutionEvent.seq)).all()
                for ev in deltas:
                    last_seq = ev.seq
                    yield frame({"artifactUpdate": {
                        "taskId": task_id, "contextId": ctx_id, "append": chunks > 0, "lastChunk": False,
                        "artifact": {"artifactId": f"{task_id}-output", "name": "output",
                                     "parts": [{"text": str((ev.payload or {}).get("text", ""))}]}}})
                    chunks += 1
                st = X(ex.status)
                if st == X.COMPLETED:
                    yield frame({"artifactUpdate": {
                        "taskId": task_id, "contextId": ctx_id, "append": chunks > 0, "lastChunk": True,
                        "artifact": {"artifactId": f"{task_id}-output", "name": "output",
                                     "parts": [{"text": "" if chunks else str((ex.output or {}).get("text") or "")}]}}})
                if task["status"]["state"] != last_state or st in FINAL:
                    last_state = task["status"]["state"]
                    yield frame({"statusUpdate": {"taskId": task_id, "contextId": ctx_id, "status": task["status"],
                                                  "metadata": task["metadata"]}})
                if st in FINAL:
                    return
            if await request.is_disconnected() or loop.time() > deadline:
                return
            await asyncio.sleep(STREAM_POLL_S)
    finally:
        for r in runners:  # inline mode only; in bus mode the worker owns the execution
            if not r.done():
                await r


def _err(id_: Any, code: int, message: str, data: Any = None) -> JSONResponse:
    e: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        e["data"] = data
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": e})


# ---- routes ----
@router.get("/agents/{identifier}/.well-known/agent-card.json")
def get_card(identifier: str, request: Request) -> JSONResponse:
    with session_factory()() as db:
        _, agent = _authorize(db, request, identifier)
        db.commit()
        return JSONResponse(agent_card(agent, str(request.base_url)))


@router.post("/agents/{identifier}")
async def rpc(identifier: str, request: Request, bg: BackgroundTasks) -> JSONResponse:
    db = session_factory()()
    try:
        key, agent = _authorize(db, request, identifier)
        db.commit()
        try:
            body = await request.json()
        except ValueError:
            return _err(None, PARSE_ERROR, "parse error")
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
            return _err(body.get("id") if isinstance(body, dict) else None, INVALID_REQUEST, "invalid request")
        id_ = body.get("id")
        version = request.headers.get("a2a-version") or request.query_params.get("A2A-Version") or "0.3"
        if version not in ("1.0", "1"):
            return _err(id_, VERSION_NOT_SUPPORTED, f"A2A version {version} not supported",
                        {"supportedVersions": [PROTOCOL_VERSION]})
        if body["method"] in STREAMING:
            try:
                task_id, inline = _open_stream(db, key, agent, body["method"], body.get("params"))
            except RpcError as e:
                db.rollback()
                return _err(id_, e.code, e.message, e.data)
            return StreamingResponse(_events(request, id_, task_id, inline), media_type="text/event-stream",
                                     headers={"A2A-Version": PROTOCOL_VERSION, "Cache-Control": "no-store",
                                              "X-Accel-Buffering": "no"})
        try:
            result = await _call(db, key, agent, body["method"], body.get("params"), bg)
        except RpcError as e:
            db.rollback()
            return _err(id_, e.code, e.message, e.data)
        except Exception:
            log.exception("a2a %s failed", body.get("method"))
            db.rollback()
            return _err(id_, INTERNAL_ERROR, "internal error")
        resp = JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})
        resp.headers["A2A-Version"] = PROTOCOL_VERSION
        return resp
    finally:
        db.close()
