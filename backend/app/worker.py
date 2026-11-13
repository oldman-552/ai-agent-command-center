"""Worker process: declares the bus topology, relays the outbox, and runs execution consumers (M10).

    uv run python -m app.worker                     # all engine queues
    WORKER_ENGINES=api,cli uv run python -m app.worker   # a specialised worker
    WORKER_ROLES=orchestrator uv run python -m app.worker  # only the orchestrator (planning + scheduling)
    WORKER_ROLES=supervisor uv run python -m app.worker    # only the agent supervisor
    WORKER_ROLES=workforce uv run python -m app.worker     # only the Arena Local Bridge health monitor (ADR-032)

AI Workforce objectives (ADR-033) need no role of their own: their planning, review and scheduling run in the
`orchestrator` role and their Automatic tasks in the `executions` role. The `workforce` role only reads bridge health.

Exits non-zero if RabbitMQ or PostgreSQL is unreachable at start, so failures are visible to the supervisor
(Docker restart policy). After start, broker outages are survived: the robust connection reconnects, consumers
are restored, and unpublished outbox rows wait in PostgreSQL.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from app.browser import engine as _browser_engine  # noqa: F401  (experimental browser engine)
from app.bus import topology as topo
from app.bus.consumer import connect
from app.bus.handlers import execution_consumers, orchestrator_consumers
from app.bus.outbox import Relay
from app.config import get_settings
from app.connectivity.a2a import engine as _a2a_engine  # noqa: F401  (registers the a2a engine)
from app.connectivity.gateway import service as _gateway  # noqa: F401  (registers the api engine)
from app.db import session_factory
from app.devplane import tools as _devplane_tools  # noqa: F401  (repo.read/files.write/git.*)
from app.governance import budgets as _budgets  # noqa: F401  (budget.increase executor)
from app.integrations import mcp_client as _mcp_client  # noqa: F401  (mcp:* tools)
from app.memory import tools as _memory_tools  # noqa: F401  (memory.write)
from app.runtime import service
from app.runtime.engines import cli as _cli_engine  # noqa: F401  (sandboxed CLI engine)
from app.runtime.engines import mock as _mock_engine  # noqa: F401  (registers the TEST-ONLY mock engine)
from app.sandbox import tools as _sandbox_tools  # noqa: F401  (registers shell.test / shell.build)
from app.workforce import control as _workforce  # noqa: F401  (AI Workforce: gateway, bridge and runtime hooks)

log = logging.getLogger("worker")


def _engines() -> tuple[str, ...]:
    raw = os.environ.get("WORKER_ENGINES", "").strip()
    return tuple(e for e in raw.split(",") if e) if raw else topo.ENGINES


async def main() -> int:
    from app.observability.logs import setup_logging
    from app.observability.tracing import setup_tracing
    setup_logging("worker")
    setup_tracing("worker")
    with session_factory()() as db:
        n = service.mark_orphans_terminated(db, service.RUNTIME_ID)
        db.commit()
    if n:
        log.warning("marked %d orphaned executions of %s as TERMINATED", n, service.RUNTIME_ID)

    conn = await connect(get_settings().rabbitmq_url)
    setup = await conn.channel()
    await topo.declare(setup)
    await setup.close()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    relay_ch = await conn.channel(publisher_confirms=True, on_return_raises=True)
    relay = asyncio.create_task(Relay(relay_ch).run(stop))
    roles = os.environ.get("WORKER_ROLES",
                           "executions,orchestrator,supervisor,automations,a2a_push,memory_index,workforce"
                           ).split(",")
    supervisor = None
    if "supervisor" in roles:  # M7: independent of the orchestrator; one leader across all workers
        from app.supervisor import service as supervisor_service
        supervisor = asyncio.create_task(supervisor_service.run(stop))
    from app.observability import live

    async def beat() -> None:  # UX-05: the API health report lists live workers and their roles
        while not stop.is_set():
            await asyncio.to_thread(live.heartbeat, "worker", {"roles": roles, "runtime_id": service.RUNTIME_ID})
            try:
                await asyncio.wait_for(stop.wait(), 15)
            except TimeoutError:
                pass
    heartbeat = asyncio.create_task(beat())
    automations = None
    if "automations" in roles:  # UX-06: in-app scheduler, never an OS service
        from app.automation import service as automation_service
        automations = asyncio.create_task(automation_service.run(stop))
    memory_index = None
    if "memory_index" in roles:  # optional semantic embeddings for memory (off unless an org configures a model)
        from app.memory import semantic as memory_semantic
        memory_index = asyncio.create_task(memory_semantic.run(stop))
    a2a_push = None
    if "a2a_push" in roles:  # A2A webhooks (transactional outbox; safe with several workers)
        from app.connectivity.a2a import push as a2a_push_service
        a2a_push = asyncio.create_task(a2a_push_service.run(stop))
    workforce = None
    consumers = (execution_consumers(_engines()) if "executions" in roles else []) + (
        orchestrator_consumers() if "orchestrator" in roles else [])
    if "orchestrator" in roles:
        from app.orchestration.scheduler import recover_running
        with session_factory()() as db:
            n = recover_running(db)
        if n:
            log.info("recovery: nudged %d active workflows", n)
    for c in consumers:
        ch = await conn.channel(publisher_confirms=True)
        await c.start(ch)
    log.info("worker %s ready (engines: %s)", service.RUNTIME_ID, ",".join(_engines()))
    print("WORKER_READY", flush=True)

    await stop.wait()
    # EV-04 graceful shutdown: 1) stop taking work, 2) let in-flight work finish within the grace period,
    # 3) interrupt what is left: it checkpoints, keeps partial output, becomes TERMINATED and — because its message
    #    was never acked — is redelivered to another worker that resumes it.
    grace = get_settings().shutdown_grace_seconds
    log.info("worker stopping: no new work; up to %ss for in-flight work", grace)
    for c in consumers:
        await c.stop_consuming()
    inflight = {t for c in consumers for t in c.inflight}
    if inflight:
        _, pending = await asyncio.wait(inflight, timeout=grace)
        if pending:
            log.warning("grace period over: handing off %d in-flight item(s)", len(pending))
            service.SHUTDOWN.set()
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    await relay
    if supervisor is not None:
        await supervisor
    if automations is not None:
        await automations
    if a2a_push is not None:
        await a2a_push
    if memory_index is not None:
        await memory_index
    if workforce is not None:
        await workforce
    await heartbeat
    await conn.close()
    log.info("worker stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
