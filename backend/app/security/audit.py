from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.security.crypto import redact, redact_obj


def log_activity(
    db: Session,
    action: str,
    *,
    category: str = "system",
    message: str = "",
    level: str = "info",
    organization_id: int | None = None,
    actor: str | None = None,
    user_id: int | None = None,
    agent_id: int | None = None,
    project_id: int | None = None,
    task_id: int | None = None,
    execution_id: str | None = None,
    details: dict[str, Any] | None = None,
    tokens: int = 0,
    cost_usd: Decimal | float | None = None,
    commit: bool = False,
) -> AuditLog:
    """Append an audit entry. All text is redacted before it reaches the database."""
    if actor is None:
        actor = f"user:{user_id}" if user_id else f"agent:{agent_id}" if agent_id else "system"
    entry = AuditLog(
        organization_id=organization_id, actor=actor, action=action, category=category, level=level,
        message=redact(message), user_id=user_id, agent_id=agent_id, project_id=project_id, task_id=task_id,
        execution_id=execution_id, details=redact_obj(details or {}), tokens=tokens,
        cost_usd=Decimal(str(cost_usd)) if cost_usd is not None else None,
    )
    db.add(entry)
    from app.observability.live import notify  # UX-02: live update after the commit (topic = category)
    notify(db, organization_id, category, action=action, project_id=project_id, task_id=task_id,
           agent_id=agent_id, execution_id=execution_id)
    if commit:
        db.commit()
    return entry
