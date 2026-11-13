"""AI Workforce (ADR-033): one Manager and its workers handle a prompt as a reviewed set of tasks.

    prompt ─► Manager: analyse ─► plan ─► assign ─► workers (Automatic via browser window · Human-Assisted · Disabled)
                                                        │
              one consolidated result ◄─ consolidate ◄─ review (approve · revise · reject · escalate to a person)

Importing this package wires the workforce into the rest of the platform (gateway hints, approvals,
execution status). `app.main` and `app.worker` import it.
"""

from typing import Any

from app.connectivity.gateway import service as _gateway
from app.control import agents as _agents
from app.models import Agent, Execution, ExecutionMode, Provider, Task
from app.runtime import service as _runtime
from app.workforce import mock as _mock  # noqa: F401  (TEST ONLY: deterministic Manager answers for the mock provider)
from app.workforce import review as _review  # noqa: F401  (Approval Center listener)
from app.workforce import scheduler as _scheduler
from app.workforce import service as _service


def _request_metadata(db, ex: Execution, agent: Agent, provider: Provider) -> dict[str, Any]:
    """Gateway hook: a worker talks to its provider under its own conversation id — and only in Automatic mode.
    Whatever created the execution, nothing is sent for a worker that is Human-Assisted or Disabled."""
    w = _service.worker_for_agent(db, agent.id)
    if w is None:
        return {}
    if w.mode != ExecutionMode.AUTOMATIC.value:
        raise _gateway.refusal("worker_not_automatic", f"worker W{w.slot} is in {w.mode} mode; the platform sends "
                                                       "nothing to its provider")
    meta: dict[str, Any] = {"session_id": w.client_session_id}
    task = db.get(Task, ex.task_id) if ex.task_id else None
    if task is not None and task.timeout_seconds:
        meta["request_timeout"] = task.timeout_seconds
    return meta


def _install() -> None:
    if _request_metadata not in _gateway.REQUEST_METADATA_HOOKS:
        _gateway.REQUEST_METADATA_HOOKS.append(_request_metadata)
    if _scheduler.on_execution_status not in _runtime.ON_STATUS:
        _runtime.ON_STATUS.append(_scheduler.on_execution_status)
    if _service.is_managed not in _agents.MANAGED_ELSEWHERE:
        _agents.MANAGED_ELSEWHERE.append(_service.is_managed)


_install()
