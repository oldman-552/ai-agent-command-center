"""A2A access keys: which external callers may reach which platform agents (A2A-01). Human-only (security.manage)."""
from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.connectivity.a2a.server import hash_key
from app.models import A2AAccessKey, Agent, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api/a2a/keys", tags=["a2a"])


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    agent_ids: list[int] = Field(min_length=1, max_length=200)


def key_out(k: A2AAccessKey) -> dict:
    return {"id": k.id, "name": k.name, "hint": k.hint, "agent_ids": k.agent_ids, "created_at": k.created_at,
            "last_used_at": k.last_used_at, "revoked_at": k.revoked_at}


@router.get("")
def list_keys(db: DB, user: User = Depends(require("security.manage"))) -> list[dict]:
    rows = db.scalars(select(A2AAccessKey).where(A2AAccessKey.organization_id == user.organization_id)
                      .order_by(A2AAccessKey.id))
    return [key_out(k) for k in rows]


@router.post("", status_code=201)
def create_key(body: KeyIn, db: DB, user: User = Depends(require("security.manage"))) -> dict:
    for aid in set(body.agent_ids):
        get_scoped(db, Agent, aid, user)  # 404 for other tenants' agents
    token = "acc_a2a_" + secrets.token_urlsafe(32)
    k = A2AAccessKey(organization_id=user.organization_id, name=body.name, key_hash=hash_key(token),
                     hint="…" + token[-4:], agent_ids=sorted(set(body.agent_ids)), created_by=user.id)
    db.add(k)
    db.flush()
    log_activity(db, "a2a.key_created", category="security", organization_id=user.organization_id,
                 actor=f"user:{user.id}", user_id=user.id, details={"key_id": k.id, "agent_ids": k.agent_ids})
    db.commit()
    return {**key_out(k), "token": token}  # shown once; only the hash is stored


@router.delete("/{key_id}")
def revoke_key(key_id: int, db: DB, user: User = Depends(require("security.manage"))) -> dict:
    k = get_scoped(db, A2AAccessKey, key_id, user)
    if k.revoked_at is None:
        k.revoked_at = datetime.now(UTC)
        log_activity(db, "a2a.key_revoked", category="security", organization_id=user.organization_id,
                     actor=f"user:{user.id}", user_id=user.id, details={"key_id": k.id})
        db.commit()
    return key_out(k)
