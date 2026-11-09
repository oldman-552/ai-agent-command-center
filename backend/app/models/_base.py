"""Shared column helpers for the schema."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import utcnow


def new_uuid() -> str:
    return str(uuid.uuid4())


def enum_check(col: str, e: type[enum.Enum]) -> CheckConstraint:
    vals = ",".join(f"'{m.value}'" for m in e)
    return CheckConstraint(f"{col} IN ({vals})", name=f"{col}_valid")


def created_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, index=True)


def org_fk() -> Mapped[int]:
    """Tenant column (ADR-007). Every org-scoped row carries it."""
    return mapped_column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
