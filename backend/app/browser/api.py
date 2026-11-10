"""Browser Bridge API (BB-02..04, ADR-005/025). EXPERIMENTAL and disabled by default.

Everything that changes the bridge needs `browser_bridge.manage`, a human-only permission. Responses never contain
cookies, storage or credentials — the bridge has no way to read them in the first place.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.browser import client, gate
from app.browser.adapters import catalog, effective, public
from app.browser.bridge import ACTIVE, BridgeError, profile_dir
from app.models import Agent, BrowserSession, Organization, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/browser-bridge", tags=["browser-bridge"])
TEST_PAGE = (Path(__file__).parent / "test_page.html").read_text()


def _http(e: BridgeError) -> HTTPException:
    return HTTPException(e.status, e.code)


def session_out(s: BrowserSession, history: bool = False) -> dict[str, Any]:
    d = {"id": s.id, "provider": s.provider_key, "profile": s.profile_name, "state": s.state, "error": s.error,
         "agent_id": s.agent_id, "model": s.model, "turns": len(s.history or []),
         "last_activity_at": s.last_activity_at, "created_at": s.created_at}
    if history:
        d["history"] = s.history or []
    return d


@router.get("/test-page", include_in_schema=False)
def test_page() -> HTMLResponse:
    """The local fake chat the bridge is tested against. Static, no data, no auth needed."""
    return HTMLResponse(TEST_PAGE, headers={"Cache-Control": "no-store",
                                            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; "
                                                                       "style-src 'unsafe-inline'; frame-src about:"})


@router.get("")
async def overview(db: DB, user: User = Depends(require("providers.read"))) -> dict:
    st = gate.state(db, user.organization_id)
    try:
        health = await client.call("GET", "/health")
    except BridgeError as e:
        health = {"reachable": False, "error": e.code}
    else:
        health = {"reachable": True, **health}
    org = db.get(Organization, user.organization_id)
    providers = [{**public(effective(a, st["selectors"].get(a.key))), "default_selectors": public(a)["selectors"],
                  "overridden": bool(st["selectors"].get(a.key)),
                  "unlocked": not a.requires_terms or a.key in st["acks"],
                  "acknowledgement": st["acks"].get(a.key),
                  "statement": gate.statement(a) if a.requires_terms else None}
                 for a in catalog(org.settings).values()]
    return {"enabled": st["enabled"], "experimental": True, "providers": providers, "health": health}


class EnabledIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.put("/enabled")
async def set_enabled(body: EnabledIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    st = gate.set_enabled(db, user.organization_id, user.id, body.enabled)
    db.commit()
    if not body.enabled:  # disabling stops every live session of the organization
        for s in db.scalars(select(BrowserSession).where(BrowserSession.organization_id == user.organization_id,
                                                         BrowserSession.state.in_(ACTIVE))).all():
            try:
                await client.call("POST", f"/sessions/{s.id}/stop")
            except BridgeError:
                s.state, s.error = "STOPPED", "bridge disabled"
        db.commit()
    return st


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=2000)


@router.post("/providers/{provider}/acknowledge")
def acknowledge(provider: str, body: AckIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    try:
        st = gate.acknowledge(db, user.organization_id, user.id, provider, body.statement)
    except gate.GateClosed as e:
        raise HTTPException(422, e.code) from e
    db.commit()
    return st


class SelectorsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selectors: dict[str, str] = Field(default_factory=dict)  # empty → back to the built-in selectors


@router.put("/providers/{provider}/selectors")
def set_selectors(provider: str, body: SelectorsIn, db: DB,
                  user: User = Depends(require("browser_bridge.manage"))) -> dict:
    """Adjust a provider's selectors when its page changed (Super Admin; audited). Applies from the next start."""
    try:
        st = gate.set_selectors(db, user.organization_id, user.id, provider, body.selectors)
    except gate.GateClosed as e:
        raise HTTPException(422, e.code) from e
    db.commit()
    return st


@router.delete("/providers/{provider}/acknowledge")
def revoke(provider: str, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    st = gate.revoke(db, user.organization_id, user.id, provider)
    db.commit()
    return st


def profile_name_for(text: str) -> str:
    import re
    n = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")[:40].strip("-")
    if n and n[0].isalnum():
        return n
    import hashlib  # names in other scripts (e.g. Arabic): a stable short name
    return f"p-{hashlib.sha256(text.encode()).hexdigest()[:10]}"


def own_profile(db, user: User, agent: Agent | None) -> str:
    """`agent-<identifier>` for an agent, the username for a person; created on first use."""
    if agent is not None:
        name, label, owner = profile_name_for(f"agent-{agent.identifier}"), agent.name, f"agent:{agent.id}"
    else:
        name, label, owner = profile_name_for(user.username), user.username, f"user:{user.id}"
    try:
        gate.ensure_profile(db, user.organization_id, user.id, name, label, owner)
    except gate.GateClosed as e:
        raise HTTPException(422, e.code) from e
    db.flush()
    return name


# ---- sites added by the Super Admin ----
class SiteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=2048)
    terms_url: str | None = Field(default=None, max_length=2048)
    input: str | None = Field(default=None, max_length=500)
    send: str | None = Field(default=None, max_length=500)
    response: str | None = Field(default=None, max_length=500)
    generating: str | None = Field(default=None, max_length=500)
    stop: str | None = Field(default=None, max_length=500)
    login: str | None = Field(default=None, max_length=500)
    new_chat: str | None = Field(default=None, max_length=500)


@router.post("/sites", status_code=201)
def add_site(body: SiteIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    """Any website, locked until its terms are acknowledged like every site (ADR-005)."""
    try:
        st = gate.add_site(db, user.organization_id, user.id, body.key, body.model_dump(exclude={"key"}))
    except gate.GateClosed as e:
        raise HTTPException(422, e.code) from e
    db.commit()
    return st


@router.delete("/sites/{key}")
async def remove_site(key: str, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    for s in db.scalars(select(BrowserSession).where(BrowserSession.organization_id == user.organization_id,
                                                     BrowserSession.provider_key == key,
                                                     BrowserSession.state.in_(ACTIVE))).all():
        try:
            await client.call("POST", f"/sessions/{s.id}/stop")
        except BridgeError:
            s.state, s.error = "STOPPED", "site removed"
    try:
        st = gate.remove_site(db, user.organization_id, user.id, key)
    except gate.GateClosed as e:
        raise HTTPException(404, e.code) from e
    db.commit()
    return st


# ---- browser profiles: one per person, agent or account ----
class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=80)
    owner: str | None = Field(default=None, max_length=40)  # "user:<id>" | "agent:<id>" | none (an account)


def _profiles_out(db, organization_id: int) -> list[dict]:
    live = {s.profile_name: s for s in db.scalars(select(BrowserSession).where(
        BrowserSession.organization_id == organization_id, BrowserSession.state.in_(ACTIVE)))}
    return [{"name": n, **p, "active_session": {"id": live[n].id, "provider": live[n].provider_key,
                                                "state": live[n].state} if n in live else None}
            for n, p in sorted(gate.profiles(db, organization_id).items())]


@router.get("/profiles")
def list_profiles(db: DB, user: User = Depends(require("providers.read"))) -> list[dict]:
    return _profiles_out(db, user.organization_id)


@router.post("/profiles", status_code=201)
def create_profile(body: ProfileIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> list[dict]:
    if body.owner and body.owner.startswith("agent:"):
        get_scoped(db, Agent, int(body.owner.split(":", 1)[1]), user)
    try:
        gate.add_profile(db, user.organization_id, user.id, body.name, body.label, body.owner)
    except gate.GateClosed as e:
        raise HTTPException(422, e.code) from e
    db.commit()
    return _profiles_out(db, user.organization_id)


@router.delete("/profiles/{name}")
async def delete_profile(name: str, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> list[dict]:
    """Deletes the profile and its stored sign-ins (cookies) from the bridge machine."""
    if any(p["name"] == name and p["active_session"] for p in _profiles_out(db, user.organization_id)):
        raise HTTPException(409, "profile_in_use")
    try:
        gate.remove_profile(db, user.organization_id, user.id, name)
    except gate.GateClosed as e:
        raise HTTPException(404, e.code) from e
    db.commit()
    try:
        await client.call("DELETE", f"/profiles/{user.organization_id}/{name}")
    except BridgeError:
        pass  # bridge offline: the directory is removed the next time (stale profiles are refused anyway)
    return _profiles_out(db, user.organization_id)


@router.get("/sessions")
def sessions(db: DB, user: User = Depends(require("providers.read"))) -> list[dict]:
    rows = db.scalars(select(BrowserSession).where(BrowserSession.organization_id == user.organization_id)
                      .order_by(BrowserSession.id.desc()).limit(100))
    return [session_out(s) for s in rows]


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=32)
    profile: str | None = Field(default=None, min_length=1, max_length=40)  # default: the agent's or your own
    agent_id: int | None = None
    test_mode: str | None = Field(default=None, max_length=20)  # only for the local test provider


@router.post("/sessions", status_code=201)
async def create_session(body: SessionIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    agent = get_scoped(db, Agent, body.agent_id, user) if body.agent_id is not None else None
    try:
        gate.check(db, user.organization_id, body.provider)  # the gate answers first
    except gate.GateClosed as e:
        raise HTTPException(403, e.code) from e
    if body.profile is None:  # every person and every agent has a browser profile of its own
        body.profile = own_profile(db, user, agent)
    elif body.profile not in gate.profiles(db, user.organization_id):
        raise HTTPException(404, "unknown_profile")
    try:
        profile_dir(user.organization_id, body.profile)
    except BridgeError as e:
        raise _http(e) from e
    if body.test_mode and body.provider != "test":
        raise HTTPException(422, "test_mode_only_for_test_provider")
    busy = db.scalar(select(BrowserSession).where(
        BrowserSession.organization_id == user.organization_id,
        BrowserSession.profile_name == body.profile, BrowserSession.state.in_(ACTIVE)))
    if busy is not None:
        raise HTTPException(409, "profile_in_use")  # a browser profile is open in one window at a time
    s = BrowserSession(organization_id=user.organization_id, provider_key=body.provider, profile_name=body.profile,
                       agent_id=body.agent_id, state="DISCONNECTED", history=[])
    db.add(s)
    db.flush()
    log_activity(db, "browser_bridge.session_created", category="browser", organization_id=user.organization_id,
                 user_id=user.id, details={"session_id": s.id, "provider": body.provider, "profile": body.profile})
    db.commit()
    try:
        return await client.call("POST", f"/sessions/{s.id}/start",
                                 {"options": {"test_mode": body.test_mode} if body.test_mode else {}})
    except BridgeError as e:
        db.refresh(s)
        if s.state == "DISCONNECTED":
            s.error = e.code
            db.commit()
        raise _http(e) from e


@router.get("/sessions/{sid}")
def get_session(sid: int, db: DB, user: User = Depends(require("providers.read"))) -> dict:
    return session_out(get_scoped(db, BrowserSession, sid, user), history=True)


class PromptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=100_000)


async def _op(sid: int, op: str, db, user: User, body: dict | None = None) -> dict:
    get_scoped(db, BrowserSession, sid, user)
    db.rollback()
    try:
        return await client.call("POST", f"/sessions/{sid}/{op}", body)
    except BridgeError as e:
        raise _http(e) from e


@router.post("/sessions/{sid}/start")
async def start(sid: int, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    return await _op(sid, "start", db, user, {"options": {}})


@router.post("/sessions/{sid}/recheck")
async def recheck(sid: int, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    return await _op(sid, "recheck", db, user)


@router.post("/sessions/{sid}/stop")
async def stop(sid: int, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    return await _op(sid, "stop", db, user)


@router.post("/sessions/{sid}/conversation")
async def new_conversation(sid: int, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    return await _op(sid, "conversation", db, user)


@router.post("/sessions/{sid}/prompt")
async def prompt(sid: int, body: PromptIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    return await _op(sid, "prompt", db, user, {"prompt": body.prompt})


@router.post("/sessions/{sid}/cancel")
async def cancel(sid: int, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    return await _op(sid, "cancel", db, user)


@router.get("/sessions/{sid}/status")
async def status(sid: int, db: DB, user: User = Depends(require("providers.read"))) -> dict:
    get_scoped(db, BrowserSession, sid, user)
    try:
        return await client.call("GET", f"/sessions/{sid}")
    except BridgeError as e:
        raise _http(e) from e
