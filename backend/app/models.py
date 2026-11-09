"""Database schema (spec T23).

Enum-like columns are stored as strings and validated by the Python enums below,
so new values (e.g. roles, providers) do not need a database enum migration.
"""
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


# ---------- enums ----------
class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    VIEWER = "viewer"


class AgentAccessLevel(str, enum.Enum):
    """Security role of an agent (spec T14). Distinct from its job role (AgentRole)."""

    MANAGER = "manager_agent"
    DEVELOPER = "developer_agent"
    REVIEWER = "reviewer_agent"
    READ_ONLY = "read_only_agent"


class AgentRuntimeStatus(str, enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class TaskStatus(str, enum.Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalKind(str, enum.Enum):
    MERGE = "merge"
    DEPLOY = "deploy"
    DELETE_DATA = "delete_data"
    SECURITY_CHANGE = "security_change"
    PERMISSION_CHANGE = "permission_change"
    HIGH_RISK = "high_risk"
    RECOMMENDATION = "recommendation"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXECUTED = "executed"


def _enum_check(col: str, e: type[enum.Enum]) -> CheckConstraint:
    vals = ",".join(f"'{m.value}'" for m in e)
    return CheckConstraint(f"{col} IN ({vals})", name=f"{col}_valid")


# ---------- users ----------
class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (_enum_check("role", UserRole),)

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default=UserRole.VIEWER.value)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    language: Mapped[str] = mapped_column(String(5), default="ar")
    token_version: Mapped[int] = mapped_column(Integer, default=0)


# ---------- credentials & providers ----------
class ApiCredential(TimestampMixin, Base):
    """Encrypted secret (API key, GitHub token). Plaintext never leaves the backend."""

    __tablename__ = "api_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))  # anthropic | openai | github | other
    encrypted_value: Mapped[str] = mapped_column(Text)
    hint: Mapped[str] = mapped_column(String(16))  # e.g. "…a1b2"
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class AgentProvider(TimestampMixin, Base):
    __tablename__ = "agent_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    kind: Mapped[str] = mapped_column(String(32))  # adapter key: anthropic | openai | mock | arena
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    base_url: Mapped[str | None] = mapped_column(String(512))
    models: Mapped[list] = mapped_column(JSONB, default=list)
    pricing: Mapped[dict] = mapped_column(JSONB, default=dict)  # model -> {input, output} USD / 1M tokens
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("api_credentials.id", ondelete="SET NULL"))
    connection_status: Mapped[str] = mapped_column(String(32), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    credential: Mapped[ApiCredential | None] = relationship()


# ---------- agents ----------
class AgentRole(TimestampMixin, Base):
    """Job role (Manager, Backend Developer, ...). Configurable, not hardcoded."""

    __tablename__ = "agent_roles"
    __table_args__ = (_enum_check("access_level", AgentAccessLevel),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name_ar: Mapped[str] = mapped_column(String(128))
    name_en: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    access_level: Mapped[str] = mapped_column(String(32), default=AgentAccessLevel.DEVELOPER.value)
    default_tools: Mapped[list] = mapped_column(JSONB, default=list)
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
        _enum_check("runtime_status", AgentRuntimeStatus),
        CheckConstraint("max_parallel_tasks >= 1", name="max_parallel_positive"),
        CheckConstraint("execution_timeout_s >= 1", name="timeout_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    identifier: Mapped[str] = mapped_column(String(64), unique=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("agent_roles.id"))
    specialty: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("agent_providers.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(128), default="")
    model_config: Mapped[dict] = mapped_column(JSONB, default=dict)  # temperature, max_tokens, ...
    allowed_tools: Mapped[list] = mapped_column(JSONB, default=list)
    github_permissions: Mapped[dict] = mapped_column(JSONB, default=dict)
    workspace_permissions: Mapped[dict] = mapped_column(JSONB, default=dict)
    max_parallel_tasks: Mapped[int] = mapped_column(Integer, default=1)
    execution_timeout_s: Mapped[int] = mapped_column(Integer, default=900)
    token_budget: Mapped[int | None] = mapped_column(BigInteger)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    runtime_status: Mapped[str] = mapped_column(String(16), default=AgentRuntimeStatus.STOPPED.value)
    last_error: Mapped[str | None] = mapped_column(Text)

    role: Mapped[AgentRole] = relationship()
    provider: Mapped[AgentProvider | None] = relationship()
    supervisor: Mapped[Agent | None] = relationship(remote_side=[id])
    projects: Mapped[list[Project]] = relationship(secondary=project_agents, back_populates="agents")


class AgentConfiguration(Base):
    """Immutable snapshot of an agent's configuration each time it changes (audit/rollback)."""

    __tablename__ = "agent_configurations"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentPermission(Base):
    """Explicit permission grant. Only a Super Admin may create these (enforced in API)."""

    __tablename__ = "agent_permissions"
    __table_args__ = (UniqueConstraint("agent_id", "permission", "project_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    permission: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    granted_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------- projects & GitHub ----------
class GitHubRepository(TimestampMixin, Base):
    __tablename__ = "github_repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), unique=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    protected_branches: Mapped[list] = mapped_column(JSONB, default=lambda: ["main", "master"])
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("api_credentials.id", ondelete="SET NULL"))


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("github_repositories.id", ondelete="SET NULL"))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)

    repository: Mapped[GitHubRepository | None] = relationship()
    agents: Mapped[list[Agent]] = relationship(secondary=project_agents, back_populates="projects")


# ---------- work ----------
class WorkRequest(TimestampMixin, Base):
    """A development request submitted by the owner; the Manager plans it into tasks."""

    __tablename__ = "work_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    plan: Mapped[dict | None] = mapped_column(JSONB)
    submitted_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    workflow_thread_id: Mapped[str | None] = mapped_column(String(64))


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (_enum_check("status", TaskStatus),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    work_request_id: Mapped[int | None] = mapped_column(ForeignKey("work_requests.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[str] = mapped_column(String(64), default="general")
    required_specialty: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.DRAFT.value)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    assigned_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    branch: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    assigned_agent: Mapped[Agent | None] = relationship(foreign_keys=[assigned_agent_id])


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (CheckConstraint("task_id <> depends_on_id", name="no_self_dependency"),)

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)


class TaskExecution(Base):
    __tablename__ = "task_executions"
    __table_args__ = (UniqueConstraint("task_id", "attempt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    log: Mapped[str] = mapped_column(Text, default="")  # secrets redacted before storage
    commands: Mapped[list] = mapped_column(JSONB, default=list)
    changed_files: Mapped[list] = mapped_column(JSONB, default=list)
    test_results: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    sender: Mapped[str] = mapped_column(String(16))  # user | agent | system | manager
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PullRequest(TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    number: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(255))
    head_branch: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32), default="open")
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRequest(TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (_enum_check("kind", ApprovalKind), _enum_check("status", ApprovalStatus))

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default=ApprovalStatus.PENDING.value)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # exact operation to execute if approved
    requested_by_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    pull_request_id: Mapped[int | None] = mapped_column(ForeignKey("pull_requests.id", ondelete="SET NULL"))
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(32), default="system", index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)  # redacted before insert
    tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)


class UsageStatistic(Base):
    __tablename__ = "usage_statistics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("agent_providers.id", ondelete="SET NULL"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    model: Mapped[str] = mapped_column(String(128))
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
