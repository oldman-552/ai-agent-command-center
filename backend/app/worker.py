"""Worker entrypoint (RabbitMQ consumers + LangGraph orchestrator).

Phase 9 implements the consumers. Until then this process only verifies it can
reach RabbitMQ and exits non-zero if it cannot, so failures are visible.
"""
import asyncio
import logging
import sys

import aio_pika

from app.config import get_settings

log = logging.getLogger("worker")


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    conn = await aio_pika.connect_robust(get_settings().rabbitmq_url)
    log.info("Connected to RabbitMQ. No consumers implemented yet (Phase 9).")
    await asyncio.Future()  # keep running
    await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
