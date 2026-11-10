"""Worker process: declares the bus topology, relays the outbox, and runs execution consumers (M10).

    uv run python -m app.worker                     # all engine queues
    WORKER_ENGINES=api,cli uv run python -m app.worker   # a specialised worker
    WORKER_ROLES=orchestrator uv run python -m app.worker  # only the orchestrator (planning + scheduling)

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

from app.bus import topology as topo
from app.bus.consumer import connect
from app.bus.handlers import execution_consumers, orchestrator_consumers
from app.bus.outbox import Relay
from app.config import get_settings
from app.connectivity.a2a import engine as _a2a_engine  # noqa: F401  (registers the a2a engine)
from app.connectivity.gateway import service as _gateway  # noqa: F401  (registers the api engine)
from app.db import session_factory
from app.devplane import tools as _devplane_tools  # noqa: F401  (repo.read/files.write/git.*)
from app.runtime import service
from app.runtime.engines import mock as _mock_engine  # noqa: F401  (registers the TEST-ONLY mock engine)
from app.sandbox import tools as _sandbox_tools  # noqa: F401  (registers shell.test / shell.build)

log = logging.getLogger("worker")


def _engines() -> tuple[str, ...]:
    raw = os.environ.get("WORKER_ENGINES", "").strip()
    return tuple(e for e in raw.split(",") if e) if raw else topo.ENGINES


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    roles = os.environ.get("WORKER_ROLES", "executions,orchestrator").split(",")
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
    await conn.close()
    log.info("worker stopped cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
