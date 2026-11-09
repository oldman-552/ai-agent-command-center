from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import ActivityLog
from app.security import redact, redact_obj


def log_activity(
    db: Session,
    action: str,
    *,
    category: str = "system",
    message: str = "",
    level: str = "info",
    user_id: int | None = None,
    agent_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
    details: dict[str, Any] | None = None,
    tokens: int = 0,
    cost_usd: Decimal | float = 0,
    commit: bool = False,
) -> ActivityLog:
    """Append an audit entry. All text is redacted before it reaches the database."""
    entry = ActivityLog(
        action=action,
        category=category,
        level=level,
        message=redact(message),
        user_id=user_id,
        agent_id=agent_id,
        project_id=project_id,
        task_id=task_id,
        details=redact_obj(details or {}),
        tokens=tokens,
        cost_usd=Decimal(str(cost_usd)),
    )
    db.add(entry)
    if commit:
        db.commit()
    return entry
