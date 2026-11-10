"""Idempotent, retrying consumer framework (MQ-02, MQ-03, MQ-04).

Per delivery:
  1. Parse + validate the envelope. Invalid / unsupported schema → rejected to the DLQ (never redelivered).
  2. Expired → DLQ.
  3. Take a PostgreSQL session advisory lock on the message's *work key* (e.g. the execution id). The lock lives on a
     dedicated connection, so if this worker dies the lock is released and the broker redelivers the un-acked
     message to a live worker. If the lock is held by a live worker, the delivery is deferred via a retry queue.
  4. Idempotency ledger: (consumer, idempotency_key) already processed → ack and skip (duplicate delivery).
  5. Run the handler (with an optional timeout).
       success → ledger 'done' is committed, then ack            (crash in between → redelivery → skipped by 4)
       Fatal   → ledger 'dead_lettered', reject → DLQ
       other   → if attempts remain: republish attempt+1 to a delay tier that returns it to this queue, then ack;
                 otherwise ledger 'dead_lettered', reject → DLQ.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractIncomingMessage, AbstractQueue
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from app.bus import topology as topo
from app.bus.contract import Envelope
from app.bus.outbox import amqp_message
from app.db import get_engine, session_factory
from app.models import ProcessedMessage

log = logging.getLogger("bus.consumer")

Handler = Callable[[Envelope], Awaitable[None]]


class Fatal(Exception):
    """Permanent failure: retrying cannot help. The message goes to the DLQ."""


class Defer(Exception):
    """Not now (e.g. the work key is busy): redeliver later without consuming an attempt."""


@dataclass
class Consumer:
    name: str                       # stable; part of the idempotency ledger key
    queue: str
    handler: Handler
    max_attempts: int = 5
    timeout_s: float | None = None  # None: the handler enforces its own deadline (executions do)
    work_key: Callable[[Envelope], str] | None = None
    prefetch: int = 4
    stats: dict[str, int] = field(default_factory=lambda: {"done": 0, "duplicate": 0, "retried": 0,
                                                           "deferred": 0, "dead_lettered": 0})
    inflight: set[asyncio.Task] = field(default_factory=set)
    _queue: AbstractQueue | None = None
    _tag: str | None = None

    # -------- ledger --------
    def _already_processed(self, key: str) -> bool:
        with get_engine().connect() as c:
            return c.execute(text("SELECT 1 FROM processed_messages WHERE consumer=:c AND idempotency_key=:k"),
                             {"c": self.name, "k": key}).first() is not None

    def _record(self, env: Envelope, outcome: str) -> None:
        with session_factory()() as db:
            db.execute(insert(ProcessedMessage).values(consumer=self.name, idempotency_key=env.idempotency_key,
                                                       message_id=env.message_id, outcome=outcome)
                       .on_conflict_do_nothing(constraint="uq_processed_messages_consumer"))
            db.commit()

    # -------- delivery --------
    async def on_message(self, msg: AbstractIncomingMessage, channel: AbstractChannel) -> None:
        try:
            env = Envelope.from_bytes(msg.body)
        except (ValidationError, ValueError) as e:
            log.error("%s: invalid message %s → DLQ: %s", self.name, msg.message_id, str(e)[:300])
            self.stats["dead_lettered"] += 1
            await msg.reject(requeue=False)
            return
        if env.expired():
            log.warning("%s: expired %s → DLQ", self.name, env.message_id)
            self.stats["dead_lettered"] += 1
            await msg.reject(requeue=False)
            return

        lock_key = f"{self.name}:{self.work_key(env) if self.work_key else env.idempotency_key}"
        conn = get_engine().connect()
        try:
            got = conn.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": lock_key}).scalar()
            conn.commit()
            if not got:
                await self._delay(channel, env, consume_attempt=False)
                self.stats["deferred"] += 1
                await msg.ack()
                return
            if self._already_processed(env.idempotency_key):
                self.stats["duplicate"] += 1
                await msg.ack()
                return
            try:
                if self.timeout_s:
                    await asyncio.wait_for(self.handler(env), self.timeout_s)
                else:
                    await self.handler(env)
            except Defer:
                await self._delay(channel, env, consume_attempt=False)
                self.stats["deferred"] += 1
                await msg.ack()
                return
            except Fatal as e:
                log.error("%s: fatal %s → DLQ: %s", self.name, env.message_id, e)
                self._record(env, "dead_lettered")
                self.stats["dead_lettered"] += 1
                await msg.reject(requeue=False)
                return
            except Exception as e:  # noqa: BLE001 — includes TimeoutError
                if env.attempt >= self.max_attempts:
                    log.error("%s: %s failed %d times → DLQ: %r", self.name, env.message_id, env.attempt, e)
                    self._record(env, "dead_lettered")
                    self.stats["dead_lettered"] += 1
                    await msg.reject(requeue=False)
                else:
                    log.warning("%s: %s attempt %d failed, retrying: %r", self.name, env.message_id, env.attempt, e)
                    await self._delay(channel, env, consume_attempt=True)
                    self.stats["retried"] += 1
                    await msg.ack()
                return
            self._record(env, "done")
            self.stats["done"] += 1
            await msg.ack()
        finally:
            try:
                conn.execute(text("SELECT pg_advisory_unlock_all()"))
                conn.commit()
            finally:
                conn.close()

    async def _delay(self, channel: AbstractChannel, env: Envelope, *, consume_attempt: bool) -> None:
        nxt = env.next_attempt() if consume_attempt else env
        tier, _ = topo.tier_for_attempt(env.attempt)
        ex = await channel.get_exchange(topo.retry_exchange(tier), ensure=True)
        # routing key = origin queue: the retry queue dead-letters it back there via the default exchange
        await ex.publish(amqp_message(nxt), routing_key=self.queue)

    async def start(self, channel: AbstractChannel) -> AbstractQueue:
        await channel.set_qos(prefetch_count=self.prefetch)
        queue = await channel.get_queue(self.queue, ensure=True)

        async def cb(msg: AbstractIncomingMessage) -> None:
            task = asyncio.current_task()
            self.inflight.add(task)
            try:
                await self.on_message(msg, channel)
            finally:
                self.inflight.discard(task)
        self._queue, self._tag = queue, await queue.consume(cb)
        log.info("consumer %s listening on %s", self.name, self.queue)
        return queue


    async def stop_consuming(self) -> None:
        """Graceful shutdown step 1: take no new deliveries (in-flight ones continue)."""
        if self._queue is not None and self._tag is not None:
            await self._queue.cancel(self._tag)
            self._tag = None


async def connect(url: str) -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(url, client_properties={"connection_name": "acc"})
