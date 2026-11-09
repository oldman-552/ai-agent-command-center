"""Providers & models API. Expanded in M4 (gateway, credentials, connection tests, routing)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.models import Provider, User
from app.security.auth import DB, require

router = APIRouter(prefix="/api", tags=["providers"])


@router.get("/providers")
def list_providers(db: DB, user: User = Depends(require("providers.read"))) -> list[dict]:
    rows = db.scalars(select(Provider).where(Provider.organization_id == user.organization_id)
                      .order_by(Provider.priority, Provider.id))
    return [{"id": p.id, "name": p.name, "kind": p.kind, "is_mock": p.is_mock, "enabled": p.enabled,
             "status": p.status, "models": [{"name": m.name, "display_name": m.display_name} for m in p.models
                                            if m.enabled]} for p in rows]
