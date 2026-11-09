"""Providers & models API (spec Phase 9): registry, encrypted credentials, connection tests, model discovery,
owner-entered pricing. API keys are write-only: responses expose only a 4-character hint."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.connectivity.gateway.base import ADAPTERS, ProviderError, ProviderUnavailable
from app.connectivity.gateway.service import build_adapter
from app.models import ApiCredential, ModelInfo, Provider, User
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.crypto import encrypt_secret, redact, secret_hint
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api", tags=["providers"])
NEEDS_KEY = {"anthropic", "openai", "google"}


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    kind: str
    base_url: str | None = Field(default=None, max_length=512, pattern=r"^https?://")
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    priority: int = Field(default=5, ge=1, le=99)
    enabled: bool = True


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, max_length=512, pattern=r"^https?://")
    priority: int | None = Field(default=None, ge=1, le=99)
    enabled: bool | None = None


class KeyIn(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    display_name: str | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    capabilities: list[str] = Field(default_factory=list)
    tier: int = Field(default=2, ge=1, le=3)
    input_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    output_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    cached_input_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    enabled: bool = True


class ModelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    context_window: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    capabilities: list[str] | None = None
    tier: int | None = Field(default=None, ge=1, le=3)
    input_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    output_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    cached_input_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    enabled: bool | None = None


def _dec(v):
    return str(v) if v is not None else None


def model_out(m: ModelInfo) -> dict:
    return {"id": m.id, "name": m.name, "display_name": m.display_name, "context_window": m.context_window,
            "max_output_tokens": m.max_output_tokens, "capabilities": m.capabilities, "tier": m.tier,
            "input_price_per_mtok": _dec(m.input_price_per_mtok), "output_price_per_mtok": _dec(m.output_price_per_mtok),
            "cached_input_price_per_mtok": _dec(m.cached_input_price_per_mtok), "enabled": m.enabled,
            "discovered": m.discovered}


def provider_out(p: Provider, full: bool = True) -> dict:
    out = {"id": p.id, "name": p.name, "kind": p.kind, "is_mock": p.is_mock, "enabled": p.enabled,
           "auth_type": p.auth_type, "base_url": p.base_url, "status": p.status, "priority": p.priority,
           "last_tested_at": p.last_tested_at, "last_error": p.last_error,
           "has_credential": p.credential is not None, "credential_hint": p.credential.hint if p.credential else None,
           "models": [model_out(m) if full else {"name": m.name, "display_name": m.display_name}
                      for m in p.models if full or m.enabled]}
    return out


def _set_key(db, p: Provider, user: User, key: str) -> None:
    cred = ApiCredential(organization_id=user.organization_id, name=f"{p.name} API key", kind=p.kind,
                         encrypted_value=encrypt_secret(key), hint=secret_hint(key), created_by_user_id=user.id)
    if p.credential:
        old = p.credential
        p.credential = cred
        db.flush()
        db.delete(old)
        cred.rotated_at = datetime.now(UTC)
    else:
        p.credential = cred
    p.status, p.last_error = "untested", None


@router.get("/providers")
def list_providers(db: DB, user: User = Depends(require("providers.read"))) -> list[dict]:
    rows = db.scalars(select(Provider).where(Provider.organization_id == user.organization_id)
                      .order_by(Provider.priority, Provider.id))
    return [provider_out(p) for p in rows]


@router.get("/provider-kinds")
def provider_kinds(_: User = Depends(require("providers.read"))) -> list[dict]:
    return [{"kind": k, "is_mock": a.is_mock, "needs_api_key": k in NEEDS_KEY,
             "needs_base_url": k == "openai_compatible"} for k, a in ADAPTERS.items()]


@router.post("/providers", status_code=201)
def create_provider(body: ProviderCreate, db: DB, user: User = Depends(require("providers.manage"))) -> dict:
    if body.kind not in ADAPTERS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_kind")
    if body.kind in NEEDS_KEY and not body.api_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "api_key_required")
    if body.kind == "openai_compatible" and not body.base_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "base_url_required")
    if body.api_key and "credentials.manage" not in {p.key for p in user.role.permissions}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    if db.scalar(select(Provider.id).where(Provider.organization_id == user.organization_id,
                                           Provider.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "name_taken")
    p = Provider(organization_id=user.organization_id, name=body.name, kind=body.kind,
                 is_mock=ADAPTERS[body.kind].is_mock, base_url=body.base_url, priority=body.priority,
                 enabled=body.enabled, auth_type="none" if body.kind == "mock" else "api_key")
    db.add(p)
    db.flush()
    if body.api_key:
        _set_key(db, p, user, body.api_key)
    log_activity(db, "providers.created", category="providers", user_id=user.id,
                 organization_id=user.organization_id, details={"provider_id": p.id, "kind": p.kind})
    db.commit()
    db.refresh(p)
    return provider_out(p)


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: int, body: ProviderPatch, db: DB,
                    user: User = Depends(require("providers.manage"))) -> dict:
    p = get_scoped(db, Provider, provider_id, user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    log_activity(db, "providers.updated", category="providers", user_id=user.id,
                 organization_id=user.organization_id, details={"provider_id": p.id})
    db.commit()
    return provider_out(p)


@router.put("/providers/{provider_id}/credential")
def set_credential(provider_id: int, body: KeyIn, db: DB,
                   user: User = Depends(require("providers.manage", "credentials.manage"))) -> dict:
    p = get_scoped(db, Provider, provider_id, user)
    _set_key(db, p, user, body.api_key)
    log_activity(db, "credentials.rotated", category="security", user_id=user.id,
                 organization_id=user.organization_id, details={"provider_id": p.id})
    db.commit()
    return provider_out(p)


@router.delete("/providers/{provider_id}", status_code=204)
def delete_provider(provider_id: int, db: DB, user: User = Depends(require("providers.manage"))) -> None:
    p = get_scoped(db, Provider, provider_id, user)
    if p.is_mock:
        raise HTTPException(status.HTTP_409_CONFLICT, "mock_provider_is_builtin")
    cred = p.credential
    log_activity(db, "providers.deleted", category="providers", level="warning", user_id=user.id,
                 organization_id=user.organization_id, details={"provider_id": p.id, "name": p.name})
    db.delete(p)
    if cred:
        db.delete(cred)
    db.commit()


@router.post("/providers/{provider_id}/test")
async def test_provider(provider_id: int, db: DB, user: User = Depends(require("providers.manage"))) -> dict:
    p = get_scoped(db, Provider, provider_id, user)
    try:
        result = await build_adapter(p).test_connection()
        p.status, p.last_error = "ok", None
    except ProviderUnavailable as e:
        p.status, p.last_error, result = "error", redact(str(e))[:500], {"ok": False, "retryable": True}
    except (ProviderError, ValueError) as e:
        p.status, p.last_error, result = "error", redact(str(e))[:500], {"ok": False, "retryable": False}
    p.last_tested_at = datetime.now(UTC)
    log_activity(db, "providers.tested", category="providers", user_id=user.id, organization_id=user.organization_id,
                 details={"provider_id": p.id, "status": p.status})
    db.commit()
    return {**result, "status": p.status, "error": p.last_error}


@router.post("/providers/{provider_id}/discover-models")
async def discover(provider_id: int, db: DB, user: User = Depends(require("providers.manage"))) -> dict:
    p = get_scoped(db, Provider, provider_id, user)
    try:
        found = await build_adapter(p).list_models()
    except (ProviderUnavailable, ProviderError) as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, redact(str(e))[:300]) from e
    existing = {m.name: m for m in p.models}
    added = 0
    for d in found:
        if d.name in existing:
            m = existing[d.name]
            m.context_window = m.context_window or d.context_window
            m.max_output_tokens = m.max_output_tokens or d.max_output_tokens
        else:
            # prices are never guessed: discovered models have no price until the owner enters one
            p.models.append(ModelInfo(name=d.name, display_name=d.display_name, context_window=d.context_window,
                                      max_output_tokens=d.max_output_tokens, discovered=True))
            added += 1
    db.commit()
    return {"discovered": len(found), "added": added}


@router.post("/providers/{provider_id}/models", status_code=201)
def add_model(provider_id: int, body: ModelIn, db: DB, user: User = Depends(require("providers.manage"))) -> dict:
    p = get_scoped(db, Provider, provider_id, user)
    if any(m.name == body.name for m in p.models):
        raise HTTPException(status.HTTP_409_CONFLICT, "model_exists")
    m = ModelInfo(**body.model_dump())
    p.models.append(m)
    db.commit()
    return model_out(m)


@router.patch("/models/{model_id}")
def update_model(model_id: int, body: ModelPatch, db: DB, user: User = Depends(require("providers.manage"))) -> dict:
    m = db.get(ModelInfo, model_id)
    if not m or m.provider.organization_id != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    log_activity(db, "models.updated", category="providers", user_id=user.id, organization_id=user.organization_id,
                 details={"model_id": m.id, "fields": sorted(body.model_dump(exclude_unset=True))})
    db.commit()
    return model_out(m)
