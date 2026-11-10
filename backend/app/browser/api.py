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
from app.browser.adapters import ADAPTERS, public
from app.browser.bridge import ACTIVE, BridgeError, profile_dir
from app.models import Agent, BrowserSession, User
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
    providers = [{**public(a), "unlocked": not a.requires_terms or a.key in st["acks"],
                  "acknowledgement": st["acks"].get(a.key),
                  "statement": gate.statement(a.key) if a.requires_terms else None} for a in ADAPTERS.values()]
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


@router.delete("/providers/{provider}/acknowledge")
def revoke(provider: str, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    st = gate.revoke(db, user.organization_id, user.id, provider)
    db.commit()
    return st


@router.get("/sessions")
def sessions(db: DB, user: User = Depends(require("providers.read"))) -> list[dict]:
    rows = db.scalars(select(BrowserSession).where(BrowserSession.organization_id == user.organization_id)
                      .order_by(BrowserSession.id.desc()).limit(100))
    return [session_out(s) for s in rows]


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=32)
    profile: str = Field(min_length=1, max_length=40)
    agent_id: int | None = None
    test_mode: str | None = Field(default=None, max_length=20)  # only for the local test provider


@router.post("/sessions", status_code=201)
async def create_session(body: SessionIn, db: DB, user: User = Depends(require("browser_bridge.manage"))) -> dict:
    try:
        gate.check(db, user.organization_id, body.provider)
        profile_dir(user.organization_id, body.provider, body.profile)
    except gate.GateClosed as e:
        raise HTTPException(403, e.code) from e
    except BridgeError as e:
        raise _http(e) from e
    if body.agent_id is not None:
        get_scoped(db, Agent, body.agent_id, user)
    if body.test_mode and body.provider != "test":
        raise HTTPException(422, "test_mode_only_for_test_provider")
    busy = db.scalar(select(BrowserSession).where(
        BrowserSession.organization_id == user.organization_id, BrowserSession.provider_key == body.provider,
        BrowserSession.profile_name == body.profile, BrowserSession.state.in_(ACTIVE)))
    if busy is not None:
        raise HTTPException(409, "profile_in_use")  # one live browser per profile directory
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
