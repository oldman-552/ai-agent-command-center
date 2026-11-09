"""Worker process: declares the bus topology, relays the outbox, and runs execution consumers (M10).

    uv run python -m app.worker                     # all engine queues
    WORKER_ENGINES=api,cli uv run python -m app.worker   # a specialised worker

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
from app.bus.handlers import execution_consumers
from app.bus.outbox import Relay
from app.config import get_settings
from app.connectivity.a2a import engine as _a2a_engine  # noqa: F401  (registers the a2a engine)
from app.connectivity.gateway import service as _gateway  # noqa: F401  (registers the api engine)
from app.db import session_factory
from app.runtime import service
from app.runtime.engines import mock as _mock_engine  # noqa: F401  (registers the TEST-ONLY mock engine)

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
    for c in execution_consumers(_engines()):
        ch = await conn.channel(publisher_confirms=True)
        await c.start(ch)
    log.info("worker %s ready (engines: %s)", service.RUNTIME_ID, ",".join(_engines()))
    print("WORKER_READY", flush=True)

    await stop.wait()
    log.info("worker stopping")
    await relay
    await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
