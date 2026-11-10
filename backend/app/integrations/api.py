"""Skills & MCP registry API (MCP-01..04, spec Phase 18, ADR-026).

Nothing external is enabled automatically: registering a server or installing a skill produces a security assessment
and leaves it disabled; enabling needs `skills.manage`, and a HIGH-risk assessment additionally needs an explicit,
audited `accept_risks`. Discovered MCP tools start disabled too and agents need `mcp:<server>:<tool>` permissions.
"""
from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.config import get_settings
from app.integrations import mcp_client
from app.integrations.assessment import assess_mcp_server, scan
from app.integrations.mcp_server import ALL_TOOLS, BROWSER_TOOLS, KEY_PREFIX, hash_key
from app.models import Agent, ApiCredential, McpAccessKey, McpServer, McpTool, Skill, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.crypto import encrypt_secret, secret_hint
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
MAX_SKILL_BYTES = 2 * 1024 * 1024
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _err(e: mcp_client.McpClientError) -> HTTPException:
    return HTTPException(e.status, e.code)


# ---------------- MCP servers ----------------
def tool_out(t: McpTool) -> dict:
    return {"id": t.id, "name": t.name, "description": t.description, "input_schema": t.input_schema,
            "permission_key": t.permission_key, "enabled": t.enabled}


def server_out(s: McpServer, tools: list[McpTool] | None = None) -> dict:
    d = {"id": s.id, "name": s.name, "transport": s.transport, "command": s.command, "url": s.url,
         "has_credential": s.credential_id is not None, "enabled": s.enabled, "status": s.status,
         "assessment": s.security_assessment, "resources": s.resources, "last_checked_at": s.last_checked_at,
         "last_error": s.last_error, "created_at": s.created_at}
    if tools is not None:
        d["tools"] = [tool_out(t) for t in tools]
    return d


class ServerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=63)
    transport: Literal["stdio", "http"]
    command: list[str] | None = Field(default=None, max_length=50)
    url: str | None = Field(default=None, max_length=512)
    token: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def _check(self):
        if not mcp_client.NAME_RE.match(self.name):
            raise ValueError("name must be lowercase letters, digits and dashes")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio servers need a command")
        if self.transport == "http" and not (self.url and re.match(r"https?://", self.url)):
            raise ValueError("http servers need an http(s) URL")
        if self.command and any(len(a) > 1000 or "\x00" in a for a in self.command):
            raise ValueError("invalid command argument")
        return self


@router.get("/mcp-servers")
def list_servers(db: DB, user: User = Depends(require("agents.read"))) -> list[dict]:
    rows = db.scalars(select(McpServer).where(McpServer.organization_id == user.organization_id).order_by(McpServer.id))
    return [server_out(s) for s in rows]


@router.post("/mcp-servers", status_code=201)
def register_server(body: ServerIn, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    if db.scalar(select(McpServer.id).where(McpServer.organization_id == user.organization_id,
                                            McpServer.name == body.name)):
        raise HTTPException(409, "name_taken")
    cred_id = None
    if body.token:
        cred = ApiCredential(organization_id=user.organization_id, name=f"MCP {body.name}", kind="mcp",
                             encrypted_value=encrypt_secret(body.token), hint=secret_hint(body.token),
                             created_by_user_id=user.id)
        db.add(cred)
        db.flush()
        cred_id = cred.id
    s = McpServer(organization_id=user.organization_id, name=body.name, transport=body.transport,
                  command=body.command if body.transport == "stdio" else None,
                  url=body.url if body.transport == "http" else None, credential_id=cred_id, enabled=False,
                  status="untested", resources=[],
                  security_assessment=assess_mcp_server(body.transport, body.command, body.url))
    db.add(s)
    db.flush()
    log_activity(db, "mcp.server_registered", category="mcp", organization_id=user.organization_id, user_id=user.id,
                 details={"server": s.name, "transport": s.transport, "risk": s.security_assessment["risk"]})
    db.commit()
    return server_out(s, [])


@router.get("/mcp-servers/{sid}")
def get_server(sid: int, db: DB, user: User = Depends(require("agents.read"))) -> dict:
    s = get_scoped(db, McpServer, sid, user)
    tools = db.scalars(select(McpTool).where(McpTool.server_id == s.id).order_by(McpTool.name)).all()
    return server_out(s, list(tools))


@router.post("/mcp-servers/{sid}/discover")
async def discover(sid: int, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    s = get_scoped(db, McpServer, sid, user)
    try:
        await mcp_client.discover(db, s)
    except mcp_client.McpClientError as e:
        raise _err(e) from e
    return get_server(sid, db, user)


@router.post("/mcp-servers/{sid}/health")
async def health(sid: int, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    s = get_scoped(db, McpServer, sid, user)
    try:
        return await mcp_client.health(db, s)
    except mcp_client.McpClientError as e:
        raise _err(e) from e


class EnableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    accept_risks: bool = False


def _risk_gate(assessment: dict | None, body: EnableIn) -> None:
    if not body.enabled:
        return
    if not assessment:
        raise HTTPException(409, "assessment_required")
    if assessment.get("risk") == "high" and not body.accept_risks:
        raise HTTPException(409, "high_risk_requires_acceptance")


@router.put("/mcp-servers/{sid}/enabled")
def enable_server(sid: int, body: EnableIn, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    s = get_scoped(db, McpServer, sid, user)
    _risk_gate(s.security_assessment, body)
    s.enabled = body.enabled
    log_activity(db, "mcp.server_enabled" if body.enabled else "mcp.server_disabled", category="security",
                 level="warning" if body.enabled and body.accept_risks else "info",
                 organization_id=user.organization_id, user_id=user.id,
                 details={"server": s.name, "risk": (s.security_assessment or {}).get("risk"),
                          "accepted_risks": body.accept_risks})
    db.commit()
    return server_out(s)


class ToolEnableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.put("/mcp-servers/{sid}/tools/{tid}")
def enable_tool(sid: int, tid: int, body: ToolEnableIn, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    s = get_scoped(db, McpServer, sid, user)
    t = db.get(McpTool, tid)
    if t is None or t.server_id != s.id:
        raise HTTPException(404, "not_found")
    t.enabled = body.enabled
    log_activity(db, "mcp.tool_enabled" if body.enabled else "mcp.tool_disabled", category="mcp",
                 organization_id=user.organization_id, user_id=user.id, details={"tool": t.permission_key})
    db.commit()
    return tool_out(t)


@router.delete("/mcp-servers/{sid}", status_code=204)
def delete_server(sid: int, db: DB, user: User = Depends(require("skills.manage"))) -> None:
    s = get_scoped(db, McpServer, sid, user)
    for a in db.scalars(select(Agent).where(Agent.organization_id == user.organization_id)):
        if s.id in (a.mcp_server_ids or []):
            a.mcp_server_ids = [x for x in a.mcp_server_ids if x != s.id]
    cred = db.get(ApiCredential, s.credential_id) if s.credential_id else None
    db.delete(s)
    if cred is not None:
        db.delete(cred)
    log_activity(db, "mcp.server_deleted", category="mcp", organization_id=user.organization_id, user_id=user.id,
                 details={"server": s.name})
    db.commit()


# ---------------- skills ----------------
def skill_out(k: Skill) -> dict:
    return {"id": k.id, "key": k.key, "name": k.name, "source": k.source, "version": k.version, "license": k.license,
            "instructions": k.instructions[:20_000], "assessment": k.assessment, "enabled": k.enabled,
            "agent_ids": k.agent_ids, "created_at": k.created_at}


def frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip().strip("'\"")
    return meta, m.group(2)


def skills_dir() -> Path:
    return Path(get_settings().skills_dir).resolve()


def _read_local(name: str) -> dict[str, str]:
    if not KEY_RE.match(name):
        raise HTTPException(422, "invalid_skill_name")
    root = (skills_dir() / name).resolve()
    if skills_dir() not in root.parents or not (root / "SKILL.md").is_file():
        raise HTTPException(404, "skill_not_found")
    files, total = {}, 0
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file() or ".git" in p.parts:
            continue
        data = p.read_bytes()[:512 * 1024]
        total += len(data)
        if total > MAX_SKILL_BYTES or len(files) >= 200:
            break
        if b"\x00" in data[:4096]:
            continue  # binary
        files[str(p.relative_to(root))] = data.decode("utf-8", "replace")
    return files


class SkillSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local: str | None = Field(default=None, max_length=128)          # a folder under SKILLS_DIR
    files: dict[str, str] | None = None                               # uploaded files, path -> text

    @model_validator(mode="after")
    def _one(self):
        if (self.local is None) == (self.files is None):
            raise ValueError("give either 'local' or 'files'")
        if self.files is not None:
            if "SKILL.md" not in self.files:
                raise ValueError("a skill needs a SKILL.md")
            if sum(len(v) for v in self.files.values()) > MAX_SKILL_BYTES or len(self.files) > 200:
                raise ValueError("skill too large")
            for p in self.files:
                if p.startswith(("/", "\\")) or ".." in Path(p).parts or len(p) > 255:
                    raise ValueError("invalid file path")
        return self

    def load(self) -> tuple[dict[str, str], str]:
        if self.local is not None:
            return _read_local(self.local), f"local:{self.local}"
        return dict(self.files or {}), "upload"


@router.get("/skills/available")
def available(user: User = Depends(require("skills.manage"))) -> list[dict]:
    root = skills_dir()
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "SKILL.md").is_file() and KEY_RE.match(d.name):
                meta, _ = frontmatter((d / "SKILL.md").read_text(errors="replace")[:20_000])
                out.append({"local": d.name, "name": meta.get("name", d.name),
                            "description": meta.get("description", "")[:500]})
    return out


@router.post("/skills/inspect")
def inspect_skill(body: SkillSource, user: User = Depends(require("skills.manage"))) -> dict:
    files, source = body.load()
    meta, _ = frontmatter(files.get("SKILL.md", ""))
    return {"source": source, "name": meta.get("name"), "description": meta.get("description"),
            "license": meta.get("license"), "files": sorted(files), "assessment": scan(files)}


@router.get("/skills")
def list_skills(db: DB, user: User = Depends(require("agents.read"))) -> list[dict]:
    rows = db.scalars(select(Skill).where(Skill.organization_id == user.organization_id).order_by(Skill.id))
    return [skill_out(k) for k in rows]


@router.post("/skills", status_code=201)
def install_skill(body: SkillSource, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    files, source = body.load()
    meta, instructions = frontmatter(files["SKILL.md"])
    key = re.sub(r"[^a-z0-9._-]+", "-", (meta.get("name") or body.local or "skill").lower()).strip("-")[:128] or "skill"
    if db.scalar(select(Skill.id).where(Skill.organization_id == user.organization_id, Skill.key == key)):
        raise HTTPException(409, "skill_exists")
    k = Skill(organization_id=user.organization_id, key=key, name=(meta.get("name") or key)[:128], source=source,
              version=(meta.get("version") or None), license=(meta.get("license") or None),
              instructions=instructions.strip()[:100_000], assessment=scan(files), enabled=False, agent_ids=[])
    db.add(k)
    db.flush()
    log_activity(db, "skills.installed", category="skills", organization_id=user.organization_id, user_id=user.id,
                 details={"skill": key, "source": source, "risk": k.assessment["risk"]})
    db.commit()
    return skill_out(k)


@router.put("/skills/{kid}/enabled")
def enable_skill(kid: int, body: EnableIn, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    k = get_scoped(db, Skill, kid, user)
    _risk_gate(k.assessment, body)
    k.enabled = body.enabled
    log_activity(db, "skills.enabled" if body.enabled else "skills.disabled", category="security",
                 level="warning" if body.enabled and body.accept_risks else "info",
                 organization_id=user.organization_id, user_id=user.id,
                 details={"skill": k.key, "risk": (k.assessment or {}).get("risk"), "accepted_risks": body.accept_risks})
    db.commit()
    return skill_out(k)


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_ids: list[int] = Field(max_length=500)


@router.put("/skills/{kid}/agents")
def assign_skill(kid: int, body: AssignIn, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    k = get_scoped(db, Skill, kid, user)
    for aid in body.agent_ids:
        get_scoped(db, Agent, aid, user)
    k.agent_ids = sorted(set(body.agent_ids))
    log_activity(db, "skills.assigned", category="skills", organization_id=user.organization_id, user_id=user.id,
                 details={"skill": k.key, "agent_ids": k.agent_ids})
    db.commit()
    return skill_out(k)


@router.delete("/skills/{kid}", status_code=204)
def delete_skill(kid: int, db: DB, user: User = Depends(require("skills.manage"))) -> None:
    k = get_scoped(db, Skill, kid, user)
    db.delete(k)
    log_activity(db, "skills.deleted", category="skills", organization_id=user.organization_id, user_id=user.id,
                 details={"skill": k.key})
    db.commit()


# ---------------- MCP access keys (the platform's own MCP server) ----------------
def key_out(k: McpAccessKey) -> dict:
    return {"id": k.id, "name": k.name, "hint": k.hint, "tools": k.tools, "created_at": k.created_at,
            "last_used_at": k.last_used_at, "revoked_at": k.revoked_at}


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    tools: list[str] = Field(min_length=1, max_length=len(ALL_TOOLS))


@router.get("/mcp-keys")
def list_keys(db: DB, user: User = Depends(require("skills.manage"))) -> dict[str, Any]:
    rows = db.scalars(select(McpAccessKey).where(McpAccessKey.organization_id == user.organization_id)
                      .order_by(McpAccessKey.id.desc()))
    return {"keys": [key_out(k) for k in rows], "tools": list(ALL_TOOLS), "browser_tools": list(BROWSER_TOOLS),
            "endpoint": "/api/mcp/"}


@router.post("/mcp-keys", status_code=201)
def create_key(body: KeyIn, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    unknown = [t for t in body.tools if t not in ALL_TOOLS]
    if unknown:
        raise HTTPException(422, f"unknown_tools: {', '.join(unknown)}")
    if set(body.tools) & set(BROWSER_TOOLS) and "browser_bridge.manage" not in {p.key for p in user.role.permissions}:
        raise HTTPException(403, "browser_tools_need_browser_bridge_manage")
    token = KEY_PREFIX + secrets.token_urlsafe(32)
    k = McpAccessKey(organization_id=user.organization_id, name=body.name, key_hash=hash_key(token),
                     hint=token[-4:], tools=sorted(set(body.tools)), created_by=user.id)
    db.add(k)
    db.flush()
    log_activity(db, "mcp_server.key_created", category="security", organization_id=user.organization_id,
                 user_id=user.id, details={"key_id": k.id, "tools": k.tools})
    db.commit()
    return {**key_out(k), "token": token}


@router.delete("/mcp-keys/{kid}")
def revoke_key(kid: int, db: DB, user: User = Depends(require("skills.manage"))) -> dict:
    k = get_scoped(db, McpAccessKey, kid, user)
    k.revoked_at = k.revoked_at or datetime.now(UTC)
    log_activity(db, "mcp_server.key_revoked", category="security", organization_id=user.organization_id,
                 user_id=user.id, details={"key_id": k.id})
    db.commit()
    return key_out(k)
