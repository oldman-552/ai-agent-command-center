"""Executions, events, checkpoints, agent messages (Phases 2, 6, 15)."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, utcnow
from app.models._base import enum_check, new_uuid, org_fk


class ExecutionStatus(str, enum.Enum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_TOOL = "WAITING_FOR_TOOL"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    CHECKPOINTED = "CHECKPOINTED"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TERMINATED = "TERMINATED"


class Execution(TimestampMixin, Base):
    """The execution contract of spec Phase 2 — one row per agent run."""

    __tablename__ = "executions"
    __table_args__ = (enum_check("status", ExecutionStatus),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    organization_id: Mapped[int] = org_fk()
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    runtime_id: Mapped[str | None] = mapped_column(String(64))  # worker/runtime instance that owns it
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    engine: Mapped[str] = mapped_column(String(16))
    input_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    tool_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(32), default=ExecutionStatus.CREATED.value, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output: Mapped[dict | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))  # None = price unknown (never fabricated)
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    parent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id", ondelete="SET NULL"))
    latest_checkpoint_seq: Mapped[int | None] = mapped_column(Integer)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    is_mock: Mapped[bool] = mapped_column(default=False)


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (UniqueConstraint("execution_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    type: Mapped[str] = mapped_column(String(32))  # state|log|output_delta|tool_call|tool_result|message|error|usage
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class Checkpoint(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("execution_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict] = mapped_column(JSONB)
    verified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentMessage(Base):
    """Structured message contract (Phase 6)."""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[str] = mapped_column(String(36), unique=True, default=new_uuid)
    organization_id: Mapped[int] = org_fk()
    sender: Mapped[str] = mapped_column(String(128))       # "user:1", "agent:5", "orchestrator"
    recipient: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    session_id: Mapped[int | None] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    message_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    schema_version: Mapped[str] = mapped_column(String(8), default="1")
    is_mock: Mapped[bool] = mapped_column(default=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
