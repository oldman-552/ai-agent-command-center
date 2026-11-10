"""MCP client (MCP-01, ADR-026): connect to registered servers, discover tools and resources, check health, and call
tools for agents — only enabled servers, only enabled tools, only for agents that were assigned the server and hold
the tool's permission key `mcp:<server>:<tool>`.

stdio servers run as a child process with a scrubbed environment (no platform secrets); HTTP servers are reached with
the server's own credential (decrypted only at call time) as a bearer token.
"""
from __future__ import annotations

import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, ApiCredential, McpServer, McpTool
from app.security.audit import log_activity
from app.security.crypto import decrypt_secret, redact, redact_obj

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TIMEOUT = 20.0
HTTPX_FACTORY = None  # tests inject an ASGI transport factory


class McpClientError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def permission_key(server: McpServer, tool: str) -> str:
    return f"mcp:{server.name}:{tool}"


def _stdio_env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp/acc-mcp-home", "LANG": "C.UTF-8"}


@asynccontextmanager
async def connect(db: Session, server: McpServer):
    if server.transport == "stdio":
        argv = list(server.command or [])
        if not argv:
            raise McpClientError("no_command", 422)
        os.makedirs("/tmp/acc-mcp-home", exist_ok=True)
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=_stdio_env())
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), TIMEOUT)
            yield s
    elif server.transport == "http":
        headers = {}
        if server.credential_id:
            cred = db.get(ApiCredential, server.credential_id)
            headers["Authorization"] = f"Bearer {decrypt_secret(cred.encrypted_value)}"
        http = (HTTPX_FACTORY or create_mcp_http_client)(headers=headers, timeout=httpx.Timeout(TIMEOUT))
        async with http, streamable_http_client(server.url, http_client=http) as (r, w, _), \
                ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), TIMEOUT)
            yield s
    else:
        raise McpClientError("unknown_transport", 422)


def _fail(db: Session, server: McpServer, e: BaseException) -> McpClientError:
    msg = redact(f"{type(e).__name__}: {e}")[:500]
    server.status, server.last_error, server.last_checked_at = "error", msg, datetime.now(UTC)
    log_activity(db, "mcp.server_error", category="mcp", level="warning", organization_id=server.organization_id,
                 details={"server": server.name, "error": msg})
    db.commit()
    return McpClientError("server_unreachable", 502)


async def discover(db: Session, server: McpServer) -> dict[str, Any]:
    """List tools and resources; new tools start disabled, vanished tools are removed."""
    try:
        async with connect(db, server) as s:
            tools = (await asyncio.wait_for(s.list_tools(), TIMEOUT)).tools
            try:
                resources = (await asyncio.wait_for(s.list_resources(), TIMEOUT)).resources
            except Exception:  # noqa: BLE001 — resources are optional in MCP
                resources = []
    except McpClientError:
        raise
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt | SystemExit | asyncio.CancelledError):
            raise
        raise _fail(db, server, e) from None
    existing = {t.name: t for t in db.scalars(select(McpTool).where(McpTool.server_id == server.id))}
    seen = set()
    for t in tools:
        seen.add(t.name)
        row = existing.get(t.name)
        if row is None:
            row = McpTool(server_id=server.id, name=t.name[:128], enabled=False,
                          permission_key=permission_key(server, t.name)[:128])
            db.add(row)
        row.description, row.input_schema = (t.description or "")[:4000], t.inputSchema or {}
    for name, row in existing.items():
        if name not in seen:
            db.delete(row)
    server.resources = [{"uri": str(r.uri), "name": r.name, "mime_type": r.mimeType} for r in resources][:500]
    server.status, server.last_error, server.last_checked_at = "ok", None, datetime.now(UTC)
    log_activity(db, "mcp.discovered", category="mcp", organization_id=server.organization_id,
                 details={"server": server.name, "tools": sorted(seen), "resources": len(server.resources)})
    db.commit()
    return {"tools": sorted(seen), "resources": server.resources}


async def health(db: Session, server: McpServer) -> dict[str, Any]:
    started = asyncio.get_running_loop().time()
    try:
        async with connect(db, server) as s:
            await asyncio.wait_for(s.send_ping(), TIMEOUT)
    except McpClientError:
        raise
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt | SystemExit | asyncio.CancelledError):
            raise
        raise _fail(db, server, e) from None
    server.status, server.last_error, server.last_checked_at = "ok", None, datetime.now(UTC)
    db.commit()
    return {"status": "ok", "latency_ms": round((asyncio.get_running_loop().time() - started) * 1000)}


def authorize(db: Session, agent: Agent, server_name: str, tool: str) -> tuple[McpServer, McpTool]:
    """Permission enforcement for an agent's MCP call. Raises McpClientError(code)."""
    server = db.scalar(select(McpServer).where(McpServer.organization_id == agent.organization_id,
                                               McpServer.name == server_name))
    if server is None:
        raise McpClientError("unknown_server", 404)
    if not server.enabled:
        raise McpClientError("server_disabled", 403)
    if server.id not in (agent.mcp_server_ids or []):
        raise McpClientError("server_not_assigned", 403)
    row = db.scalar(select(McpTool).where(McpTool.server_id == server.id, McpTool.name == tool))
    if row is None:
        raise McpClientError("unknown_tool", 404)
    if not row.enabled:
        raise McpClientError("tool_disabled", 403)
    return server, row


async def call_tool(db: Session, server: McpServer, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        async with connect(db, server) as s:
            res = await asyncio.wait_for(s.call_tool(tool, arguments or {}), 120)
    except McpClientError:
        raise
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt | SystemExit | asyncio.CancelledError):
            raise
        raise _fail(db, server, e) from None
    content = []
    for c in res.content:
        content.append({"type": c.type, "text": getattr(c, "text", None)} if c.type == "text" else {"type": c.type})
    return redact_obj({"is_error": bool(res.isError), "content": content,
                       "structured": getattr(res, "structuredContent", None)})


# ---------------- runtime tool: agents call `mcp:<server>:<tool>` ----------------
async def runtime_tool(ex, call: dict[str, Any]) -> dict[str, Any]:
    from app.db import session_factory
    name, args = str(call["tool"]), dict(call.get("arguments") or {})
    _, server_name, tool = name.split(":", 2)
    with session_factory()() as db:
        agent = db.get(Agent, ex.agent_id)
        try:
            server, _row = authorize(db, agent, server_name, tool)
            result = await call_tool(db, server, tool, args)
        except McpClientError as e:
            log_activity(db, "mcp.call_denied" if e.status in (403, 404) else "mcp.call_failed", category="mcp",
                         level="warning", organization_id=ex.organization_id, agent_id=ex.agent_id,
                         execution_id=ex.id, details={"tool": name, "reason": e.code})
            db.commit()
            return {"error": e.code}
        log_activity(db, "mcp.call", category="mcp", organization_id=ex.organization_id, agent_id=ex.agent_id,
                     execution_id=ex.id, details={"tool": name, "is_error": result["is_error"]})
        db.commit()
        return result


def _register() -> None:
    from app.runtime.service import register_tool
    register_tool("mcp:*", runtime_tool)


_register()
