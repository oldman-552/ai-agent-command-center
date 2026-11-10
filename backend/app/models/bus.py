"""Internal message bus persistence (spec Phase 6.1, ADR-014).

- `outbox_messages`: transactional outbox. A message is written in the same DB transaction as the state change
  that caused it; the relay publishes it to RabbitMQ with publisher confirms. No lost or phantom messages.
- `processed_messages`: consumer-side idempotency ledger, unique per (consumer, idempotency_key).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, utcnow


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), unique=True)
    exchange: Mapped[str] = mapped_column(String(64))
    routing_key: Mapped[str] = mapped_column(String(128))
    envelope: Mapped[dict] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ProcessedMessage(Base):
    __tablename__ = "processed_messages"
    __table_args__ = (UniqueConstraint("consumer", "idempotency_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consumer: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(160))
    message_id: Mapped[str] = mapped_column(String(36))
    outcome: Mapped[str] = mapped_column(String(16))  # done | dead_lettered
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CircuitBreaker(Base):
    """Circuit breakers for agents and providers (EV-03). closed → open after N consecutive failures; after the
    cool-down one trial is let through (half_open); its success closes the circuit, its failure re-opens it."""

    __tablename__ = "circuit_breakers"
    __table_args__ = (UniqueConstraint("scope", "ref_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(Integer, index=True)
    scope: Mapped[str] = mapped_column(String(16))  # agent | provider
    ref_id: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="closed")  # closed | open | half_open
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
