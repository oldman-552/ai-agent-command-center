"""RabbitMQ topology (MQ-01, ADR-014). Declared idempotently by every API/worker process at start.

    acc.commands  (topic)  -- "execution.run.<engine>" -->  acc.work.execution.<engine>   (priority 0-9)
    acc.events    (topic)  -- "<category>.<verb>"     -->  subscriber queues (declared by their consumers)
    acc.retry.<tier> (fanout) --> acc.retry.<tier> queue (TTL) -- dead-letter via "" + original key --> origin queue
    acc.dlx       (fanout) --> acc.dlq                       (poison, expired and retry-exhausted messages)

Work queues are classic durable queues because RabbitMQ priorities need classic queues. Messages are persistent
and published with confirms; consumers ack manually after the work is recorded.
"""
from __future__ import annotations

from dataclasses import dataclass

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

from app.models.agents import ExecutionEngineKind

COMMANDS = "acc.commands"
EVENTS = "acc.events"
DLX = "acc.dlx"
DLQ = "acc.dlq"
MAX_PRIORITY = 9

# Retry tiers: attempt n waits RETRY_TIERS[min(n-1, last)] seconds before redelivery to its origin queue.
RETRY_TIERS: tuple[tuple[str, int], ...] = (("5s", 5), ("30s", 30), ("300s", 300))
ENGINES = tuple(k.value for k in ExecutionEngineKind) + ("mock",)


def work_queue(engine: str) -> str:
    return f"acc.work.execution.{engine}"


def run_routing_key(engine: str) -> str:
    return f"execution.run.{engine}"


def retry_exchange(tier: str) -> str:
    return f"acc.retry.{tier}"


def tier_for_attempt(attempt: int) -> tuple[str, int]:
    return RETRY_TIERS[min(max(attempt, 1) - 1, len(RETRY_TIERS) - 1)]


@dataclass
class Topology:
    commands: AbstractExchange
    events: AbstractExchange
    dlx: AbstractExchange
    dlq: AbstractQueue
    retry: dict[str, AbstractExchange]
    work: dict[str, AbstractQueue]


async def declare(ch: AbstractChannel) -> Topology:
    durable = {"durable": True}
    commands = await ch.declare_exchange(COMMANDS, aio_pika.ExchangeType.TOPIC, **durable)
    events = await ch.declare_exchange(EVENTS, aio_pika.ExchangeType.TOPIC, **durable)
    dlx = await ch.declare_exchange(DLX, aio_pika.ExchangeType.FANOUT, **durable)
    dlq = await ch.declare_queue(DLQ, durable=True, arguments={"x-queue-mode": "lazy"})
    await dlq.bind(dlx)

    retry: dict[str, AbstractExchange] = {}
    for tier, seconds in RETRY_TIERS:
        ex = await ch.declare_exchange(retry_exchange(tier), aio_pika.ExchangeType.FANOUT, **durable)
        # On TTL expiry the message is dead-lettered to the default exchange with the routing key it was
        # published with — which is the origin queue's name — so it returns to exactly that queue.
        q = await ch.declare_queue(retry_exchange(tier), durable=True, arguments={
            "x-message-ttl": seconds * 1000, "x-dead-letter-exchange": ""})
        await q.bind(ex)
        retry[tier] = ex

    work: dict[str, AbstractQueue] = {}
    for engine in ENGINES:
        q = await declare_consumer_queue(ch, work_queue(engine))
        await q.bind(commands, run_routing_key(engine))
        work[engine] = q
    return Topology(commands, events, dlx, dlq, retry, work)


async def declare_consumer_queue(ch: AbstractChannel, name: str) -> AbstractQueue:
    """Durable, priority-capable queue whose rejected messages go to the DLQ."""
    return await ch.declare_queue(name, durable=True, arguments={
        "x-max-priority": MAX_PRIORITY, "x-dead-letter-exchange": DLX})


async def declare_event_subscription(ch: AbstractChannel, name: str, patterns: list[str]) -> AbstractQueue:
    events = await ch.declare_exchange(EVENTS, aio_pika.ExchangeType.TOPIC, durable=True)
    q = await declare_consumer_queue(ch, name)
    for p in patterns:
        await q.bind(events, p)
    return q
