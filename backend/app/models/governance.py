"""Memory, evaluation, approvals, audit, usage, cost, budgets, automations (Phases 8, 14, 16, 20, 24)."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Computed, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, utcnow
from app.models._base import enum_check, org_fk


class MemoryType(str, enum.Enum):
    AGENT_IDENTITY = "agent_identity"
    PROJECT = "project"
    TASK = "task"
    CONVERSATION = "conversation"
    TECHNICAL_DECISION = "technical_decision"
    CODEBASE = "codebase"
    EXECUTION = "execution"
    LESSON = "lesson"
    TEAM_SHARED = "team_shared"


class Verification(str, enum.Enum):
    VERIFIED = "verified"
    ASSUMPTION = "assumption"
    SUGGESTION = "suggestion"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXECUTED = "executed"
    EXPIRED = "expired"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Memory(TimestampMixin, Base):
    __tablename__ = "memories"
    __table_args__ = (
        enum_check("memory_type", MemoryType),
        enum_check("verification", Verification),
        Index("ix_memories_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    memory_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[dict] = mapped_column(JSONB, default=dict)  # provenance: {kind, ref, execution_id, user_id}
    verification: Mapped[str] = mapped_column(String(16), default=Verification.ASSUMPTION.value)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("memories.id", ondelete="SET NULL"))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    # optional provider embedding (memory/semantic.py); used only when made by the configured model
    semantic_embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float))
    semantic_model: Mapped[str | None] = mapped_column(String(160))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(content,''))", persisted=True)
    )


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    task_type: Mapped[str | None] = mapped_column(String(32))
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)  # dimension -> 0..1 or null if not measured
    overall: Mapped[float | None] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(16))  # approve|revise|reject
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Approval(TimestampMixin, Base):
    __tablename__ = "approvals"
    __table_args__ = (enum_check("status", ApprovalStatus), enum_check("risk_level", RiskLevel))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    operation: Mapped[str] = mapped_column(String(64))  # merge|deploy|destructive_db|credential_change|...
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.HIGH.value)
    status: Mapped[str] = mapped_column(String(32), default=ApprovalStatus.PENDING.value, index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # exact operation executed only after approval
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id", ondelete="SET NULL"))
    pull_request_id: Mapped[int | None] = mapped_column(ForeignKey("pull_requests.id", ondelete="SET NULL"))
    requested_by: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"),
                                                        index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")  # user:1 | agent:2 | supervisor | system
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), default="system", index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # redacted before insert
    tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = org_fk()
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    provider_kind: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id", ondelete="SET NULL"), index=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cached_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)


class CostRecord(Base):
    """Cost derived from a usage record and owner-entered prices. Absent when the price is unknown."""

    __tablename__ = "cost_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = org_fk()
    usage_record_id: Mapped[int] = mapped_column(ForeignKey("usage_records.id", ondelete="CASCADE"), unique=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    pricing: Mapped[dict] = mapped_column(JSONB, default=dict)  # prices used, for audit


class Budget(TimestampMixin, Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    scope: Mapped[str] = mapped_column(String(16))  # global|agent|project|task|provider
    scope_id: Mapped[int | None] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(16))  # daily|monthly|total
    limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    limit_tokens: Mapped[int | None] = mapped_column(BigInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Automation(TimestampMixin, Base):
    """In-app scheduled/event automations. Never installed as OS services (Phase 24)."""

    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    trigger: Mapped[dict] = mapped_column(JSONB)  # {"type":"cron","expr":"0 3 * * *"} | {"type":"event",...}
    action: Mapped[dict] = mapped_column(JSONB)   # {"type":"workflow","objective":...}
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|paused
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")  # e.g. event cursor
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AutomationRun(Base):
    """One execution of an automation (UX-06 history)."""

    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    automation_id: Mapped[int] = mapped_column(ForeignKey("automations.id", ondelete="CASCADE"), index=True)
    organization_id: Mapped[int] = org_fk()
    trigger: Mapped[str] = mapped_column(String(32))  # schedule | event | manual
    status: Mapped[str] = mapped_column(String(16))   # succeeded | failed | skipped
    detail: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
