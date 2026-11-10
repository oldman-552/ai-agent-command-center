"""Which tools an API-engine agent is offered, as provider-neutral specs (name, description, JSON Schema).

Offered = the agent's tool permissions that the runtime implements and has a schema for, plus the enabled tools of
the MCP servers assigned to the agent that it is permitted to call. Being offered a tool grants nothing: every call
still goes through the runtime's permission check, policy and audit (service.call_tool). Model-facing names must
match `^[A-Za-z0-9_-]{1,64}$` for every provider, so platform keys (`repo.read`, `mcp:srv:tool`) are encoded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, Execution, McpServer, McpTool
from app.runtime.tools import TOOL_SCHEMAS, TOOLS

MAX_TOOLS = 64


@dataclass
class ToolSpec:
    name: str                      # model-facing
    key: str                       # platform tool key
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})


def model_name(key: str, taken: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]", "_", key)[:60] or "tool"
    name, n = base, 2
    while name in taken:
        name, n = f"{base[:58]}_{n}", n + 1
    taken.add(name)
    return name


def _schema(s: Any) -> dict[str, Any]:
    return s if isinstance(s, dict) and s.get("type") == "object" else {"type": "object", "properties": {}}


def for_execution(db: Session, ex: Execution, agent: Agent) -> list[ToolSpec]:
    from app.runtime.service import _TOOLS
    perms = list(ex.tool_permissions or [])
    taken: set[str] = set()
    specs: list[ToolSpec] = []
    for key in perms:
        if key in TOOL_SCHEMAS and key in _TOOLS:
            specs.append(ToolSpec(model_name(key, taken), key, TOOLS.get(key, key), TOOL_SCHEMAS[key]))
    mcp_perms = {k for k in perms if k.startswith("mcp:")}
    if mcp_perms and agent.mcp_server_ids:
        rows = db.execute(select(McpServer.name, McpTool.name, McpTool.description, McpTool.input_schema)
                          .join(McpTool, McpTool.server_id == McpServer.id)
                          .where(McpServer.id.in_(agent.mcp_server_ids), McpServer.enabled.is_(True),
                                 McpServer.organization_id == agent.organization_id, McpTool.enabled.is_(True))
                          .order_by(McpServer.name, McpTool.name)).all()
        for server, tool, desc, schema in rows:
            key = f"mcp:{server}:{tool}"
            if key in mcp_perms:
                specs.append(ToolSpec(model_name(key, taken), key,
                                      f"[external MCP server {server}] {(desc or tool)[:1000]}", _schema(schema)))
    return specs[:MAX_TOOLS]
