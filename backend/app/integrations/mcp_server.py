"""The platform's own MCP server (MCP-02, ADR-026) at `/api/mcp` (streamable HTTP, stateless, JSON responses).

Access needs an MCP access key (`Authorization: Bearer acc_mcp_…`). A key lists exactly which tools it may call —
nothing by default. Every call re-authenticates from the request, is scoped to the key's organization, and is
audited. Browser Bridge tools additionally pass the bridge gate (disabled by default, per-provider terms).
"""
from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from sqlalchemy import select

from app.browser import client as bridge_client
from app.browser import gate
from app.browser.adapters import catalog, public
from app.browser.bridge import ACTIVE, BridgeError, profile_dir
from app.db import session_factory
from app.models import Agent, Approval, BrowserSession, McpAccessKey, Organization, Task
from app.security.audit import log_activity

server = FastMCP("AI Agent Command Center", instructions="Platform tools of the AI Agent Command Center. "
                 "Browser Bridge tools are EXPERIMENTAL and only work when an administrator enabled them.")

BROWSER_TOOLS = ("list_providers", "get_provider_status", "list_available_models", "create_browser_session",
                 "close_browser_session", "create_conversation", "resume_conversation", "send_prompt",
                 "get_generation_status", "get_conversation_history", "cancel_generation", "get_bridge_health")
PLATFORM_TOOLS = ("list_agents", "list_tasks", "get_task", "search_memory", "list_pending_approvals")
ALL_TOOLS = PLATFORM_TOOLS + BROWSER_TOOLS
KEY_PREFIX = "acc_mcp_"


class Denied(Exception):
    pass


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _key_from(authorization: str | None) -> McpAccessKey | None:
    if not authorization or not authorization.startswith(f"Bearer {KEY_PREFIX}"):
        return None
    with session_factory()() as db:
        k = db.scalar(select(McpAccessKey).where(McpAccessKey.key_hash == hash_key(authorization[7:].strip()),
                                                 McpAccessKey.revoked_at.is_(None)))
        if k is not None:
            db.expunge(k)
        return k


def _auth(ctx: Context, tool: str) -> McpAccessKey:
    req = ctx.request_context.request
    key = _key_from(req.headers.get("authorization") if req is not None else None)
    if key is None:
        raise Denied("unauthorized")
    with session_factory()() as db:
        allowed = tool in (key.tools or [])
        log_activity(db, "mcp_server.call" if allowed else "mcp_server.denied", category="mcp",
                     level="info" if allowed else "warning", organization_id=key.organization_id,
                     actor=f"mcp_key:{key.id}", details={"tool": tool})
        row = db.get(McpAccessKey, key.id)
        row.last_used_at = datetime.now(UTC)
        db.commit()
    if not allowed:
        raise Denied(f"tool '{tool}' is not granted to this key")
    return key


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


# ---------------- platform tools (read-only) ----------------
@server.tool(description="List the organization's agents (id, name, role, state).")
def list_agents(ctx: Context) -> str:
    key = _auth(ctx, "list_agents")
    with session_factory()() as db:
        rows = db.scalars(select(Agent).where(Agent.organization_id == key.organization_id,
                                              Agent.archived_at.is_(None)).order_by(Agent.id))
        return _json([{"id": a.id, "name": a.name, "role": a.role, "state": a.state} for a in rows])


@server.tool(description="List tasks, optionally of one project.")
def list_tasks(ctx: Context, project_id: int | None = None, limit: int = 50) -> str:
    key = _auth(ctx, "list_tasks")
    with session_factory()() as db:
        stmt = select(Task).where(Task.organization_id == key.organization_id)
        if project_id is not None:
            stmt = stmt.where(Task.project_id == project_id)
        rows = db.scalars(stmt.order_by(Task.id.desc()).limit(max(1, min(limit, 200))))
        return _json([{"id": t.id, "title": t.title, "status": t.status, "project_id": t.project_id} for t in rows])


@server.tool(description="Get one task with its status, acceptance criteria and result summary.")
def get_task(ctx: Context, task_id: int) -> str:
    key = _auth(ctx, "get_task")
    with session_factory()() as db:
        t = db.get(Task, task_id)
        if t is None or t.organization_id != key.organization_id:
            return _json({"error": "not_found"})
        return _json({"id": t.id, "title": t.title, "status": t.status, "description": t.description,
                      "acceptance_criteria": t.acceptance_criteria, "error": t.error,
                      "output": str((t.result or {}).get("output", ""))[:4000]})


@server.tool(description="Search a project's memories (ranked by relevance). Returns current, unexpired memories.")
def search_memory(ctx: Context, project_id: int, query: str, limit: int = 10) -> str:
    key = _auth(ctx, "search_memory")
    from app.memory import store
    from app.models import Project
    with session_factory()() as db:
        p = db.get(Project, project_id)
        if p is None or p.organization_id != key.organization_id:
            return _json({"error": "not_found"})
        hits = store.search(db, organization_id=key.organization_id, query=query, project_id=project_id,
                            limit=max(1, min(limit, 20)))
        return _json([{"id": h.memory.id, "title": h.memory.title, "content": h.memory.content,
                       "verification": h.memory.verification, "score": round(h.score, 3)} for h in hits])


@server.tool(description="List approval requests waiting for a human decision (read-only; MCP cannot decide).")
def list_pending_approvals(ctx: Context) -> str:
    key = _auth(ctx, "list_pending_approvals")
    with session_factory()() as db:
        rows = db.scalars(select(Approval).where(Approval.organization_id == key.organization_id,
                                                 Approval.status == "pending").order_by(Approval.id.desc()).limit(100))
        return _json([{"id": a.id, "operation": a.operation, "risk": a.risk_level, "title": a.title} for a in rows])


# ---------------- Browser Bridge tools (EXPERIMENTAL) ----------------
def _session(key: McpAccessKey, session_id: int) -> BrowserSession:
    with session_factory()() as db:
        s = db.get(BrowserSession, session_id)
        if s is None or s.organization_id != key.organization_id:
            raise Denied("session not found")
        db.expunge(s)
        return s


async def _bridge(method: str, path: str, body: dict | None = None) -> str:
    try:
        return _json(await bridge_client.call(method, path, body))
    except BridgeError as e:
        return _json({"error": e.code})


@server.tool(description="List Browser Bridge providers and whether each is unlocked (EXPERIMENTAL).")
def list_providers(ctx: Context) -> str:
    key = _auth(ctx, "list_providers")
    with session_factory()() as db:
        st = gate.state(db, key.organization_id)
        sites = catalog(db.get(Organization, key.organization_id).settings)
    return _json({"enabled": st["enabled"], "providers": [
        {**public(a), "unlocked": not a.requires_terms or a.key in st["acks"]} for a in sites.values()]})


@server.tool(description="Status of one Browser Bridge provider: enabled, unlocked, live sessions.")
def get_provider_status(ctx: Context, provider: str) -> str:
    key = _auth(ctx, "get_provider_status")
    with session_factory()() as db:
        if gate.adapter(db, key.organization_id, provider) is None:
            return _json({"error": "unknown_provider"})
        try:
            gate.check(db, key.organization_id, provider)
            usable, reason = True, None
        except gate.GateClosed as e:
            usable, reason = False, e.code
        live = db.scalars(select(BrowserSession).where(BrowserSession.organization_id == key.organization_id,
                                                       BrowserSession.provider_key == provider,
                                                       BrowserSession.state.in_(ACTIVE))).all()
    return _json({"provider": provider, "usable": usable, "reason": reason,
                  "sessions": [{"id": s.id, "state": s.state} for s in live]})


@server.tool(description="Models known for a Browser Bridge provider (informational).")
def list_available_models(ctx: Context, provider: str) -> str:
    key = _auth(ctx, "list_available_models")
    with session_factory()() as db:
        a = gate.adapter(db, key.organization_id, provider)
    return _json({"provider": provider, "models": list(a.models) if a else []})


@server.tool(description="Start a browser session for a site in an existing browser profile (EXPERIMENTAL).")
async def create_browser_session(ctx: Context, provider: str, profile: str) -> str:
    key = _auth(ctx, "create_browser_session")
    with session_factory()() as db:
        try:
            gate.check(db, key.organization_id, provider)
            profile_dir(key.organization_id, profile)
        except gate.GateClosed as e:
            return _json({"error": e.code})
        except BridgeError as e:
            return _json({"error": e.code})
        if profile not in gate.profiles(db, key.organization_id):
            return _json({"error": "unknown_profile"})
        busy = db.scalar(select(BrowserSession.id).where(
            BrowserSession.organization_id == key.organization_id,
            BrowserSession.profile_name == profile, BrowserSession.state.in_(ACTIVE)))
        if busy:
            return _json({"error": "profile_in_use", "session_id": busy})
        s = BrowserSession(organization_id=key.organization_id, provider_key=provider, profile_name=profile,
                           state="DISCONNECTED", history=[])
        db.add(s)
        db.commit()
        sid = s.id
    return await _bridge("POST", f"/sessions/{sid}/start", {"options": {}})


@server.tool(description="Close a browser session.")
async def close_browser_session(ctx: Context, session_id: int) -> str:
    key = _auth(ctx, "close_browser_session")
    _session(key, session_id)
    return await _bridge("POST", f"/sessions/{session_id}/stop")


@server.tool(description="Start a new conversation in a browser session.")
async def create_conversation(ctx: Context, session_id: int) -> str:
    key = _auth(ctx, "create_conversation")
    _session(key, session_id)
    return await _bridge("POST", f"/sessions/{session_id}/conversation")


@server.tool(description="Resume a browser session (restart it if it stopped) and return its conversation.")
async def resume_conversation(ctx: Context, session_id: int) -> str:
    key = _auth(ctx, "resume_conversation")
    s = _session(key, session_id)
    if s.state not in ACTIVE:
        await bridge_client.call("POST", f"/sessions/{session_id}/start", {"options": {}})
    s = _session(key, session_id)
    return _json({"session_id": s.id, "state": s.state, "history": s.history})


@server.tool(description="Send a prompt in a browser session; poll get_generation_status for the answer.")
async def send_prompt(ctx: Context, session_id: int, prompt: str) -> str:
    key = _auth(ctx, "send_prompt")
    _session(key, session_id)
    return await _bridge("POST", f"/sessions/{session_id}/prompt", {"prompt": prompt})


@server.tool(description="State of a browser session and the partial answer while generating.")
async def get_generation_status(ctx: Context, session_id: int) -> str:
    key = _auth(ctx, "get_generation_status")
    _session(key, session_id)
    return await _bridge("GET", f"/sessions/{session_id}")


@server.tool(description="Conversation history (visible text only) of a browser session.")
def get_conversation_history(ctx: Context, session_id: int) -> str:
    key = _auth(ctx, "get_conversation_history")
    s = _session(key, session_id)
    return _json({"session_id": s.id, "history": s.history})


@server.tool(description="Cancel the generation running in a browser session.")
async def cancel_generation(ctx: Context, session_id: int) -> str:
    key = _auth(ctx, "cancel_generation")
    _session(key, session_id)
    return await _bridge("POST", f"/sessions/{session_id}/cancel")


@server.tool(description="Browser Bridge health.")
async def get_bridge_health(ctx: Context) -> str:
    _auth(ctx, "get_bridge_health")
    return await _bridge("GET", "/health")


# ---------------- ASGI endpoint ----------------
class McpEndpoint:
    """Mounted at /api/mcp. A fresh session manager per application lifespan (the SDK's manager runs only once)."""

    def __init__(self) -> None:
        self.manager: StreamableHTTPSessionManager | None = None

    @asynccontextmanager
    async def running(self):
        mgr = StreamableHTTPSessionManager(app=server._mcp_server, stateless=True, json_response=True)
        async with mgr.run():
            self.manager = mgr
            try:
                yield
            finally:
                self.manager = None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if self.manager is None or _key_from(headers.get("authorization")) is None:
            status = 503 if self.manager is None else 401
            body = json.dumps({"detail": "unavailable" if status == 503 else "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.manager.handle_request(scope, receive, send)


endpoint = McpEndpoint()
