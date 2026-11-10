"""Platform consumers. Workers call the runtime service — the runtime itself is unchanged by the bus."""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.bus import topology as topo
from app.bus.consumer import Consumer, Fatal
from app.bus.contract import Envelope, MessageType
from app.bus.outbox import enqueue
from app.db import session_factory
from app.models import Execution, Task
from app.models import ExecutionStatus as X
from app.runtime import service
from app.runtime.contract import TERMINAL

log = logging.getLogger("bus.handlers")

# Statuses that mean "a runtime was working on it". We hold the execution's work lock, so whoever set them is gone.
_LOST_TO_TERMINATED = {X.RUNNING, X.WAITING_FOR_TOOL, X.CHECKPOINTED}
_LOST_TO_FAILED = {X.INITIALIZING, X.READY, X.RECOVERING}
# Legitimately parked until a human/tool answers — another message will wake them.
_PARKED = {X.WAITING_FOR_APPROVAL, X.WAITING_FOR_INPUT}


def _event(ex: Execution, type_: MessageType, causation: Envelope, **payload) -> Envelope:
    return Envelope(message_type=type_, sender=f"worker:{service.RUNTIME_ID}", recipient="*",
                    organization_id=ex.organization_id, project_id=ex.project_id, task_id=ex.task_id,
                    correlation_id=ex.id, causation_id=causation.message_id,
                    idempotency_key=f"{type_}:{ex.id}:{causation.message_id}",
                    payload={"execution_id": ex.id, **payload})


async def handle_execution_run(env: Envelope) -> None:
    ex_id = str(env.payload.get("execution_id") or "")
    with session_factory()() as db:
        ex = db.get(Execution, ex_id)
        if ex is None:
            raise Fatal(f"unknown execution {ex_id!r}")
        if ex.organization_id != env.organization_id:
            raise Fatal("organization mismatch")  # never act across tenants on a forged/mis-routed message
        st = X(ex.status)
        if st in TERMINAL or st in _PARKED:
            return
        if st == X.FAILED and not str(env.payload.get("reason", "")).startswith("resume"):
            return  # the runtime already gave up (its retry budget is spent); only an explicit resume reruns it
        if st in _LOST_TO_TERMINATED | _LOST_TO_FAILED:
            to = X.TERMINATED if st in _LOST_TO_TERMINATED else X.FAILED
            service.set_status(db, ex, to, f"runtime {ex.runtime_id} lost; recovered by {service.RUNTIME_ID}")
            enqueue(db, _event(ex, MessageType.RECOVERY_ORPHANS_TERMINATED, env, previous_status=st.value,
                               previous_runtime=ex.runtime_id))
            db.commit()
            log.warning("execution %s: recovered from lost runtime %s (%s)", ex.id, ex.runtime_id, st.value)
        enqueue(db, _event(ex, MessageType.EXECUTION_STARTED, env, attempt=env.attempt, reason=env.payload.get("reason")))
        db.commit()

    final = await service.run_execution(ex_id)

    with session_factory()() as db:
        ex = db.get(Execution, ex_id)
        enqueue(db, _event(ex, MessageType.EXECUTION_FINISHED, env, status=final,
                           error_category=ex.error_category))
        db.commit()


def execution_consumers(engines: tuple[str, ...] = topo.ENGINES) -> list[Consumer]:
    """One consumer per engine specialty queue. Business failures are recorded on the execution by the runtime
    (with its own retry budget); the bus only retries infrastructure failures (DB down, crash before ack)."""
    return [Consumer(name=f"execution.run.{e}", queue=topo.work_queue(e), handler=handle_execution_run,
                     work_key=lambda env: f"execution:{env.payload.get('execution_id')}", max_attempts=5)
            for e in engines]


# ---------------- orchestrator (M6) ----------------
async def handle_orchestrator_command(env: Envelope) -> None:
    from app.bus.consumer import Defer
    from app.models import Workflow
    from app.orchestration import planner, scheduler

    wf_id = int(env.payload.get("workflow_id") or 0)
    with session_factory()() as db:
        wf = db.get(Workflow, wf_id)
        if wf is None or wf.organization_id != env.organization_id:
            raise Fatal(f"unknown workflow {wf_id}")
        status, thread_id = wf.status, wf.thread_id
    if env.message_type == MessageType.WORKFLOW_PLAN:
        if status != "planning":
            return  # already planned (duplicate or recovery nudge)
        if await planner.run_planning(wf_id, thread_id) == "planned":
            with session_factory()() as db:
                scheduler.request_advance(db, db.get(Workflow, wf_id), "planned", env)
                db.commit()
        return
    if env.message_type == MessageType.WORKFLOW_ADVANCE:
        with session_factory()() as db:
            summary = scheduler.advance(db, wf_id, env)
        transient = {"all_capable_agents_busy", "agent_concurrency_limit"}  # permanent reasons wait for a human nudge
        if (set((summary.get("waiting") or {}).values()) & transient and not summary.get("dispatched")
                and summary.get("status") == "running"):
            with session_factory()() as db:
                busy = db.scalar(select(func.count()).select_from(Task).where(
                    Task.workflow_id == wf_id, Task.status == "IN_PROGRESS"))
            if not busy:
                raise Defer()  # nothing will wake us (agents busy elsewhere): look again shortly
        return
    raise Fatal(f"unexpected orchestrator command {env.message_type}")


async def handle_execution_finished(env: Envelope) -> None:
    from app.orchestration import scheduler

    ex_id = str(env.payload.get("execution_id") or "")
    with session_factory()() as db:
        await scheduler.on_execution_finished(db, ex_id, env)


def orchestrator_consumers() -> list[Consumer]:
    return [
        Consumer(name="orchestrator.commands", queue=topo.ORCHESTRATOR, handler=handle_orchestrator_command,
                 work_key=lambda env: f"workflow:{env.payload.get('workflow_id')}", max_attempts=5),
        Consumer(name="orchestrator.execution-events", queue=topo.ORCHESTRATOR_EVENTS,
                 handler=handle_execution_finished, max_attempts=5),
    ]
