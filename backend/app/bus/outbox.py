"""Transactional outbox + relay (MQ-02, MQ-04).

`enqueue()` adds the message to the caller's DB transaction — it is published only if that transaction commits,
and it is never lost if the broker is down at that moment. The relay (run by every worker) claims unpublished rows
with `FOR UPDATE SKIP LOCKED`, publishes them persistently with publisher confirms, and marks them published.
If the broker is unreachable the rows simply wait; delivery resumes when it returns (at-least-once; consumers
deduplicate by idempotency key).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import aio_pika
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bus import topology as topo
from app.bus.contract import Envelope
from app.db import session_factory
from app.models import OutboxMessage

log = logging.getLogger("bus.outbox")


def route(env: Envelope) -> tuple[str, str]:
    """(exchange, routing key) for an envelope. Commands go to a specialty work queue, events fan out."""
    if env.message_type == "execution.run":
        engine = str(env.payload.get("engine") or env.recipient.split(":", 1)[-1])
        return topo.COMMANDS, topo.run_routing_key(engine)
    if env.is_command:
        return topo.COMMANDS, env.message_type.value
    return topo.EVENTS, env.message_type.value


def enqueue(db: Session, env: Envelope) -> OutboxMessage:
    exchange, key = route(env)
    row = OutboxMessage(message_id=env.message_id, exchange=exchange, routing_key=key,
                        envelope=env.model_dump(mode="json"), priority=env.priority,
                        trace_context=current_trace_context())
    db.add(row)
    return row


def current_trace_context() -> dict[str, str] | None:
    """The active span's W3C trace context, so the consumer's span joins the producer's trace."""
    from opentelemetry.propagate import inject
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier or None


def amqp_message(env: Envelope, trace: dict[str, str] | None = None) -> aio_pika.Message:
    expiration = None
    if env.expires_at is not None:
        expiration = max((env.expires_at - datetime.now(UTC)).total_seconds(), 0.001)
    return aio_pika.Message(
        body=env.to_bytes(), content_type="application/json", delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        message_id=env.message_id, correlation_id=env.correlation_id, priority=env.priority, type=env.message_type,
        timestamp=env.timestamp, expiration=expiration,
        headers={"schema_version": env.schema_version, "attempt": env.attempt, "sender": env.sender,
                 **(trace or {})})


class Relay:
    def __init__(self, channel: aio_pika.abc.AbstractChannel, *, batch: int = 100, interval: float = 0.25):
        self.channel, self.batch, self.interval = channel, batch, interval
        self._exchanges: dict[str, aio_pika.abc.AbstractExchange] = {}

    async def _exchange(self, name: str) -> aio_pika.abc.AbstractExchange:
        if name not in self._exchanges:
            self._exchanges[name] = await self.channel.get_exchange(name, ensure=True)
        return self._exchanges[name]

    async def publish_pending(self) -> int:
        """Publish one batch. Returns how many were confirmed by the broker."""
        sent = 0
        with session_factory()() as db:
            rows = db.scalars(select(OutboxMessage).where(OutboxMessage.published_at.is_(None))
                              .order_by(OutboxMessage.priority.desc(), OutboxMessage.id)
                              .limit(self.batch).with_for_update(skip_locked=True)).all()
            for row in rows:
                try:
                    env = Envelope.model_validate(row.envelope)
                    ex = await self._exchange(row.exchange)
                    # confirmed by the broker; commands must reach a queue, events may have no subscriber yet
                    await ex.publish(amqp_message(env, row.trace_context), routing_key=row.routing_key,
                                     mandatory=row.exchange == topo.COMMANDS)
                    row.published_at = datetime.now(UTC)
                    sent += 1
                except Exception as e:
                    row.attempts += 1
                    row.last_error = f"{type(e).__name__}: {e}"[:2000]
                    db.commit()
                    raise
            db.commit()
        return sent

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                n = await self.publish_pending()
            except Exception as e:  # noqa: BLE001 — broker down etc.; rows stay pending
                log.warning("outbox relay: %s", e)
                n = 0
            if n < self.batch:
                try:
                    await asyncio.wait_for(stop.wait(), self.interval)
                except TimeoutError:
                    pass
