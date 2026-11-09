"""Tenant isolation helpers (ADR-007). Every org-scoped query must go through these."""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import Select
from sqlalchemy.orm import Session

from app.models import User


def scoped(stmt: Select, model, user: User) -> Select:
    return stmt.where(model.organization_id == user.organization_id)


def get_scoped[T](db: Session, model: type[T], obj_id, user: User) -> T:
    """Fetch by id within the user's organization; other tenants' rows are indistinguishable from missing."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "organization_id", None) != user.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    return obj
