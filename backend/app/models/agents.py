"""Agents, templates, lifecycle, sessions (Phase 3)."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin, utcnow
from app.models._base import enum_check, org_fk


class AgentState(str, enum.Enum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    IDLE = "IDLE"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DISABLED = "DISABLED"


class ExecutionEngineKind(str, enum.Enum):
    API = "api"
    CLI = "cli"
    BROWSER = "browser"
    A2A = "a2a"


class SecurityProfile(str, enum.Enum):
    """Agent security profile — never a user role (ADR-006)."""

    MANAGER = "manager"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    READ_ONLY = "read_only"


class AgentTemplate(TimestampMixin, Base):
    __tablename__ = "agent_templates"
    __table_args__ = (enum_check("security_profile", SecurityProfile),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(64))
    description_ar: Mapped[str] = mapped_column(Text, default="")
    description_en: Mapped[str] = mapped_column(Text, default="")
    system_instructions: Mapped[str] = mapped_column(Text, default="")
    tool_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    security_profile: Mapped[str] = mapped_column(String(16), default=SecurityProfile.DEVELOPER.value)
    context_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    memory_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


project_agents = Table(
    "project_agents",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("agent_id", ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True),
)


class Agent(TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("organization_id", "identifier"),
        enum_check("state", AgentState),
        enum_check("execution_engine", ExecutionEngineKind),
        enum_check("security_profile", SecurityProfile),
        CheckConstraint("concurrency_limit >= 1", name="concurrency_positive"),
        CheckConstraint("timeout_seconds >= 1", name="timeout_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    identifier: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    role: Mapped[str] = mapped_column(String(64))
    avatar: Mapped[str | None] = mapped_column(String(64))  # icon key or color token, never a remote URL
    template_id: Mapped[int | None] = mapped_column(ForeignKey("agent_templates.id", ondelete="SET NULL"))
    system_instructions: Mapped[str] = mapped_column(Text, default="")
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    model: Mapped[str | None] = mapped_column(String(128))
    fallback_provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id", ondelete="SET NULL"))
    fallback_model: Mapped[str | None] = mapped_column(String(128))
    execution_engine: Mapped[str] = mapped_column(String(16), default=ExecutionEngineKind.API.value)
    tool_permissions: Mapped[list] = mapped_column(JSONB, default=list)
    skills: Mapped[list] = mapped_column(JSONB, default=list)
    mcp_server_ids: Mapped[list] = mapped_column(JSONB, default=list)
    memory_profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    context_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    security_profile: Mapped[str] = mapped_column(String(16), default=SecurityProfile.DEVELOPER.value)
    token_budget: Mapped[int | None] = mapped_column(BigInteger)
    cost_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=900)
    retry_policy: Mapped[dict] = mapped_column(JSONB, default=lambda: {"max_attempts": 3, "backoff_seconds": 5,
                                                                        "backoff_multiplier": 2.0})
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    supervisor_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    state: Mapped[str] = mapped_column(String(32), default=AgentState.CREATED.value)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    last_error: Mapped[str | None] = mapped_column(Text)

    projects: Mapped[list[Project]] = relationship(secondary=project_agents, lazy="selectin")  # noqa: F821


class AgentConfigVersion(Base):
    __tablename__ = "agent_config_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    changed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentStateTransition(Base):
    __tablename__ = "agent_state_transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(32))
    to_state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64))  # "user:3", "supervisor", "runtime", "orchestrator"
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16), default="chat")  # chat | execution
    status: Mapped[str] = mapped_column(String(16), default="open")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
