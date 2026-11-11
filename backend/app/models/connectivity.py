"""Providers, models, credentials, browser sessions, MCP, skills (Phases 9, 10, 11, 18)."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin
from app.models._base import enum_check, org_fk


class AuthType(str, enum.Enum):
    """Kept separate per spec Phase 9: API keys, local CLI, browser session, external agent service."""

    API_KEY = "api_key"
    CLI = "cli"
    BROWSER = "browser"
    EXTERNAL = "external"
    NONE = "none"


class BrowserSessionState(str, enum.Enum):
    DISCONNECTED = "DISCONNECTED"
    STARTING = "STARTING"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    READY = "READY"
    SENDING = "SENDING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class ApiCredential(TimestampMixin, Base):
    """Encrypted secret. Plaintext never leaves the backend and never enters a prompt."""

    __tablename__ = "api_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))  # anthropic|openai|google|github|mcp|other
    encrypted_value: Mapped[str] = mapped_column(Text)
    hint: Mapped[str] = mapped_column(String(16))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Provider(TimestampMixin, Base):
    __tablename__ = "providers"
    __table_args__ = (UniqueConstraint("organization_id", "name"), enum_check("auth_type", AuthType))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    name: Mapped[str] = mapped_column(String(128))
    # adapter key: anthropic|openai|google|openai_compatible|arena_local_bridge|a2a|mock
    kind: Mapped[str] = mapped_column(String(32))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_type: Mapped[str] = mapped_column(String(16), default=AuthType.API_KEY.value)
    base_url: Mapped[str | None] = mapped_column(String(512))
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("api_credentials.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="untested")  # untested|ok|error|rate_limited
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    rate_limits: Mapped[dict] = mapped_column(JSONB, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    # Per-provider adapter options (e.g. Arena Local Bridge: Host header, timeouts, terms acknowledgement). No secrets.
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # Last health snapshot taken by the platform (sanitised: account e-mail addresses are masked).
    health: Mapped[dict | None] = mapped_column(JSONB)

    credential: Mapped[ApiCredential | None] = relationship(lazy="joined")
    models: Mapped[list[ModelInfo]] = relationship(back_populates="provider", lazy="selectin",
                                                   cascade="all, delete-orphan")


class ModelInfo(TimestampMixin, Base):
    """A model offered by a provider. Prices are owner-entered; NULL means unknown (never invented)."""

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("provider_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    display_name: Mapped[str | None] = mapped_column(String(128))
    context_window: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    capabilities: Mapped[list] = mapped_column(JSONB, default=list)  # tools, vision, long_context, reasoning, cheap
    tier: Mapped[int] = mapped_column(Integer, default=2)  # 1 fast/cheap … 3 most capable (for routing)
    input_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    output_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cached_input_price_per_mtok: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    discovered: Mapped[bool] = mapped_column(Boolean, default=False)

    provider: Mapped[Provider] = relationship(back_populates="models")


class BrowserSession(TimestampMixin, Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (enum_check("state", BrowserSessionState),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    provider_key: Mapped[str] = mapped_column(String(32))  # claude|chatgpt|gemini|test
    profile_name: Mapped[str] = mapped_column(String(64))   # dedicated isolated profile, never a personal one
    state: Mapped[str] = mapped_column(String(16), default=BrowserSessionState.DISCONNECTED.value)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    conversation_ref: Mapped[str | None] = mapped_column(String(255))
    history: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")  # [{role, text, at}] — text only
    model: Mapped[str | None] = mapped_column(String(128))
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class McpServer(TimestampMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    name: Mapped[str] = mapped_column(String(128))
    transport: Mapped[str] = mapped_column(String(16))  # stdio | http
    command: Mapped[list | None] = mapped_column(JSONB)  # stdio argv
    url: Mapped[str | None] = mapped_column(String(512))
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("api_credentials.id", ondelete="SET NULL"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # enabled only after security review
    status: Mapped[str] = mapped_column(String(16), default="untested")
    security_assessment: Mapped[dict | None] = mapped_column(JSONB)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    resources: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")  # discovered resources


class McpTool(TimestampMixin, Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("mcp_servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(JSONB, default=dict)
    permission_key: Mapped[str] = mapped_column(String(128))  # e.g. "mcp:<server>:<tool>"
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("organization_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(512))  # repo URL or local path
    version: Mapped[str | None] = mapped_column(String(64))
    license: Mapped[str | None] = mapped_column(String(64))
    instructions: Mapped[str] = mapped_column(Text, default="")
    assessment: Mapped[dict | None] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    agent_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")  # assignment


class A2AAccessKey(TimestampMixin, Base):
    """Inbound A2A access (A2A-01). Nothing is exposed by default: a key lists exactly which agents it may reach.
    Only a SHA-256 of the high-entropy token is stored; the token is shown once at creation."""

    __tablename__ = "a2a_access_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(16))
    agent_ids: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class A2APushConfig(TimestampMixin, Base):
    """A2A push notification config (spec §4.3.1) of one inbound task. The token and credentials are encrypted and
    write-only; deliveries stop after repeated failures (disabled_at)."""

    __tablename__ = "a2a_push_configs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    access_key_id: Mapped[int] = mapped_column(ForeignKey("a2a_access_keys.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(2048))
    token_encrypted: Mapped[str | None] = mapped_column(Text)
    auth_scheme: Mapped[str | None] = mapped_column(String(32))
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    last_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class A2APushDelivery(Base):
    """One webhook POST to make (transactional outbox: written with the status change, sent by a worker)."""

    __tablename__ = "a2a_push_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_id: Mapped[str] = mapped_column(ForeignKey("a2a_push_configs.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class McpAccessKey(TimestampMixin, Base):
    """Inbound access to the platform's MCP server (MCP-02). A key lists exactly which platform tools it may call;
    only a SHA-256 of the token is stored and the token is shown once."""

    __tablename__ = "mcp_access_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    name: Mapped[str] = mapped_column(String(128))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    hint: Mapped[str] = mapped_column(String(16))
    tools: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
