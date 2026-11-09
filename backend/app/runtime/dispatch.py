"""Hand an execution to whoever runs it (ADR-014). The only place that knows the dispatch mode."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.bus.contract import execution_run
from app.bus.outbox import enqueue
from app.config import get_settings
from app.models import Execution
from app.runtime import service


def dispatch(db: Session, ex: Execution, actor: str, reason: str,
             run_inline: Callable[..., Any] | None = None) -> None:
    """Bus mode: an outbox command committed with the caller's transaction; a worker runs it.
    Inline mode (tests/debug only): `run_inline(service.run_execution, ex.id)` schedules it in-process."""
    if get_settings().execution_dispatch == "inline":
        if run_inline is not None:
            run_inline(service.run_execution, ex.id)
        return
    enqueue(db, execution_run(execution_id=ex.id, organization_id=ex.organization_id, engine=ex.engine,
                              sender=actor, reason=reason, project_id=ex.project_id, task_id=ex.task_id))
