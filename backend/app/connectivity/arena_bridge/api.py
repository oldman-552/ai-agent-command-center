"""Arena Local Bridge settings API (ADR-032): instances, key, terms gate, diagnostics, health, discovery.

Reading needs `workforce.read`. Creating, changing or deleting a bridge, storing its key and acknowledging the terms
need `workforce.bridge` — a human-only permission that only the Super Admin role holds, like the Browser Bridge's —
and storing a key also needs `credentials.manage`. Diagnostics, health and discovery (none of which sends anything to
Arena) need `workforce.manage`. The key is write-only: responses carry a 4-character hint, never the key.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.connectivity.arena_bridge import service as bridge
from app.connectivity.arena_bridge.adapter import KIND
from app.connectivity.arena_bridge.client import BridgeError
from app.models import Agent, Provider, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.crypto import redact
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/workforce/bridges", tags=["workforce"])


class BridgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(default=bridge.DEFAULT_URL, max_length=512)
    api_key: str = Field(min_length=8, max_length=512)
    settings: dict[str, Any] = Field(default_factory=dict)


class BridgePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    settings: dict[str, Any] | None = None


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(min_length=8, max_length=512)


class TermsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(max_length=4000)


def _load(db, bridge_id: int, user: User) -> Provider:
    p = get_scoped(db, Provider, bridge_id, user)
    if p.kind != KIND:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return p


def bridge_out(db, p: Provider) -> dict[str, Any]:
    cfg = bridge.config_of(p)
    ack = (p.settings or {}).get("terms_ack")
    used_by = db.scalars(select(Agent.id).where(Agent.provider_id == p.id, Agent.archived_at.is_(None))).all()
    return {
        "id": p.id, "name": p.name, "kind": p.kind, "base_url": p.base_url, "enabled": p.enabled, "is_mock": p.is_mock,
        "status": p.status, "last_tested_at": p.last_tested_at, "last_error": p.last_error,
        "has_key": p.credential is not None, "key_hint": p.credential.hint if p.credential else None,
        "settings": {"host_header": cfg.host_header, "effective_host_header": cfg.effective_host_header,
                     "stream": cfg.stream, "connect_timeout_s": cfg.connect_timeout,
                     "idle_timeout_s": cfg.idle_timeout, "request_timeout_s": cfg.request_timeout,
                     "replay_window_s": cfg.replay_window, "read_session_registry": cfg.read_session_registry,
                     "max_prompt_chars": cfg.max_prompt_chars, "max_concurrent": cfg.max_concurrent},
        "terms": {"acknowledged": bool(ack), "by": (ack or {}).get("by"), "at": (ack or {}).get("at"),
                  "terms_url": bridge.TERMS_URL, "statement": bridge.statement(p)},
        "health": p.health, "health_fresh": bridge.health_is_fresh(p), "agent_ids": list(used_by),
    }


def _config_error(e: bridge.BridgeConfigError) -> HTTPException:
    code = str(e)
    return HTTPException(status.HTTP_409_CONFLICT if code == "name_taken" else status.HTTP_422_UNPROCESSABLE_CONTENT,
                         code)


@router.get("")
def list_bridges(db: DB, user: User = Depends(require("workforce.read"))) -> dict[str, Any]:
    rows = db.scalars(select(Provider).where(Provider.organization_id == user.organization_id, Provider.kind == KIND)
                      .order_by(Provider.id))
    return {"default_url": bridge.DEFAULT_URL, "in_container": bridge.in_container(),
            "bridges": [bridge_out(db, p) for p in rows]}


@router.post("", status_code=201)
def create_bridge(body: BridgeIn, db: DB,
                  user: User = Depends(require("workforce.bridge", "credentials.manage"))) -> dict[str, Any]:
    try:
        p = bridge.create(db, organization_id=user.organization_id, user_id=user.id, name=body.name,
                          base_url=body.base_url, key=body.api_key, settings=body.settings)
    except bridge.BridgeConfigError as e:
        raise _config_error(e) from e
    db.commit()
    db.refresh(p)
    return bridge_out(db, p)


@router.get("/{bridge_id}")
def get_bridge(bridge_id: int, db: DB, user: User = Depends(require("workforce.read"))) -> dict[str, Any]:
    return bridge_out(db, _load(db, bridge_id, user))


@router.patch("/{bridge_id}")
def update_bridge(bridge_id: int, body: BridgePatch, db: DB,
                  user: User = Depends(require("workforce.bridge"))) -> dict[str, Any]:
    p = _load(db, bridge_id, user)
    try:
        bridge.update(db, p, user.id, **body.model_dump(exclude_unset=True))
    except bridge.BridgeConfigError as e:
        raise _config_error(e) from e
    db.commit()
    return bridge_out(db, p)


@router.put("/{bridge_id}/key")
def set_key(bridge_id: int, body: KeyIn, db: DB,
            user: User = Depends(require("workforce.bridge", "credentials.manage"))) -> dict[str, Any]:
    p = _load(db, bridge_id, user)
    bridge.set_key(db, p, user.id, body.api_key)
    log_activity(db, "credentials.rotated", category="security", user_id=user.id,
                 organization_id=user.organization_id, details={"provider_id": p.id, "kind": KIND})
    db.commit()
    return bridge_out(db, p)


@router.delete("/{bridge_id}", status_code=204)
def delete_bridge(bridge_id: int, db: DB, user: User = Depends(require("workforce.bridge"))) -> None:
    from app.models import WorkforceWorker
    p = _load(db, bridge_id, user)
    in_use = db.scalar(select(WorkforceWorker.id).join(Agent, Agent.id == WorkforceWorker.agent_id).where(
        Agent.provider_id == p.id, WorkforceWorker.mode == "AUTOMATIC").limit(1))
    if in_use:
        raise HTTPException(status.HTTP_409_CONFLICT, "bridge_in_use")  # switch those workers first
    cred = p.credential
    log_activity(db, "workforce.bridge_deleted", category="workforce", level="warning", user_id=user.id,
                 organization_id=user.organization_id, details={"provider_id": p.id, "name": p.name})
    db.delete(p)
    if cred is not None:
        db.delete(cred)
    db.commit()


@router.post("/{bridge_id}/terms")
def acknowledge_terms(bridge_id: int, body: TermsIn, db: DB,
                      user: User = Depends(require("workforce.bridge"))) -> dict[str, Any]:
    p = _load(db, bridge_id, user)
    try:
        bridge.acknowledge(db, p, user.id, body.statement)
    except bridge.BridgeConfigError as e:
        raise _config_error(e) from e
    db.commit()
    return bridge_out(db, p)


@router.delete("/{bridge_id}/terms")
def revoke_terms(bridge_id: int, db: DB, user: User = Depends(require("workforce.bridge"))) -> dict[str, Any]:
    p = _load(db, bridge_id, user)
    bridge.revoke(db, p, user.id)
    db.commit()
    return bridge_out(db, p)


@router.post("/{bridge_id}/test")
async def test_bridge(bridge_id: int, db: DB, user: User = Depends(require("workforce.manage"))) -> dict[str, Any]:
    """Connection test with a step-by-step diagnosis, run from where the API runs. Sends nothing to Arena."""
    p = _load(db, bridge_id, user)
    try:
        report = await bridge.diagnose(db, p)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, redact(str(e))[:200]) from e
    log_activity(db, "workforce.bridge_tested", category="workforce", user_id=user.id,
                 organization_id=user.organization_id,
                 details={"provider_id": p.id, "ok": report["ok"],
                          "failed": [s["step"] for s in report["steps"] if s["ok"] is False]})
    db.commit()
    return {**report, "bridge": bridge_out(db, p)}


@router.post("/{bridge_id}/health")
async def refresh_health(bridge_id: int, db: DB, user: User = Depends(require("workforce.manage"))) -> dict[str, Any]:
    p = _load(db, bridge_id, user)
    await bridge.refresh_health(db, p)
    db.commit()
    return bridge_out(db, p)


@router.post("/{bridge_id}/discover")
async def discover(bridge_id: int, db: DB, user: User = Depends(require("workforce.manage"))) -> dict[str, Any]:
    p = _load(db, bridge_id, user)
    try:
        found = await bridge.discover(db, p)
    except BridgeError as e:
        db.rollback()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{e.code}") from e
    db.commit()
    return found
