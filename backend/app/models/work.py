"""Projects, repositories, workflows, tasks (Phases 12, 13)."""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db import Base, TimestampMixin
from app.models._base import enum_check, org_fk


class ProjectMode(str, enum.Enum):
    NEW = "new"
    EXISTING = "existing"
    MAINTENANCE = "maintenance"


class TaskStatus(str, enum.Enum):
    BACKLOG = "BACKLOG"
    PLANNED = "PLANNED"
    READY = "READY"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    REVIEW = "REVIEW"
    APPROVAL = "APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskLifecycle(str, enum.Enum):
    """The detailed, persisted lifecycle of a task (AI Workforce). `TaskStatus` stays the scheduler's coarse phase and
    is always a function of this value (`COARSE_STATUS`), enforced by the model and by a table constraint."""

    DRAFT = "DRAFT"
    PLANNED = "PLANNED"
    READY = "READY"                                  # dependencies satisfied, waiting for an eligible worker
    ASSIGNED = "ASSIGNED"
    WAITING_FOR_ACCEPTANCE = "WAITING_FOR_ACCEPTANCE"  # Human-Assisted: the task card waits for a person
    ACCEPTED = "ACCEPTED"
    QUEUED = "QUEUED"                                # Automatic: dispatched, not started yet
    RUNNING = "RUNNING"
    SUBMITTED = "SUBMITTED"                          # a result arrived (from a bridge or from a person)
    UNDER_REVIEW = "UNDER_REVIEW"
    APPROVED = "APPROVED"                            # review passed; waits only for a required human approval
    REJECTED = "REJECTED"
    REVISION_REQUESTED = "REVISION_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class ExecutionMode(str, enum.Enum):
    AUTOMATIC = "AUTOMATIC"
    HUMAN_ASSISTED = "HUMAN_ASSISTED"
    DISABLED = "DISABLED"


_L, _T = TaskLifecycle, TaskStatus
COARSE_STATUS: dict[str, str] = {
    _L.DRAFT.value: _T.BACKLOG.value, _L.PLANNED.value: _T.PLANNED.value, _L.READY.value: _T.READY.value,
    _L.REVISION_REQUESTED.value: _T.READY.value, _L.BLOCKED.value: _T.BLOCKED.value,
    _L.ASSIGNED.value: _T.IN_PROGRESS.value, _L.WAITING_FOR_ACCEPTANCE.value: _T.IN_PROGRESS.value,
    _L.ACCEPTED.value: _T.IN_PROGRESS.value, _L.QUEUED.value: _T.IN_PROGRESS.value,
    _L.RUNNING.value: _T.IN_PROGRESS.value, _L.SUBMITTED.value: _T.IN_PROGRESS.value,
    _L.UNDER_REVIEW.value: _T.REVIEW.value, _L.APPROVED.value: _T.APPROVAL.value,
    _L.COMPLETED.value: _T.COMPLETED.value, _L.REJECTED.value: _T.FAILED.value, _L.FAILED.value: _T.FAILED.value,
    _L.CANCELLED.value: _T.CANCELLED.value,
}
# What a plain `task.status = …` means for code that does not know the detailed lifecycle.
DEFAULT_LIFECYCLE: dict[str, str] = {
    _T.BACKLOG.value: _L.DRAFT.value, _T.PLANNED.value: _L.PLANNED.value, _T.READY.value: _L.READY.value,
    _T.IN_PROGRESS.value: _L.RUNNING.value, _T.BLOCKED.value: _L.BLOCKED.value, _T.REVIEW.value: _L.UNDER_REVIEW.value,
    _T.APPROVAL.value: _L.APPROVED.value, _T.COMPLETED.value: _L.COMPLETED.value, _T.FAILED.value: _L.FAILED.value,
    _T.CANCELLED.value: _L.CANCELLED.value,
}


def lifecycle_pairs_sql() -> str:
    """CHECK expression: `lifecycle` and `status` can only appear in consistent pairs."""
    by_status: dict[str, list[str]] = {}
    for fine, coarse in COARSE_STATUS.items():
        by_status.setdefault(coarse, []).append(fine)
    return " OR ".join("(status = '{}' AND lifecycle IN ({}))".format(c, ", ".join(f"'{f}'" for f in sorted(fs)))
                       for c, fs in sorted(by_status.items()))


class Repository(TimestampMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("organization_id", "full_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    host: Mapped[str] = mapped_column(String(32), default="github")
    full_name: Mapped[str] = mapped_column(String(255))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    protected_branches: Mapped[list] = mapped_column(JSONB, default=lambda: ["main", "master"])
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("api_credentials.id", ondelete="SET NULL"))
    analysis: Mapped[dict | None] = mapped_column(JSONB)  # tech detection / codebase map (existing-project mode)
    api_base: Mapped[str] = mapped_column(String(255), default="https://api.github.com",
                                          server_default="https://api.github.com")  # GitHub Enterprise
    webhook_credential_id: Mapped[int | None] = mapped_column(ForeignKey("api_credentials.id", ondelete="SET NULL"))


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "name"), enum_check("mode", ProjectMode))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16), default=ProjectMode.NEW.value)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    analysis: Mapped[dict | None] = mapped_column(JSONB)  # OR-09 repository map (existing/maintenance projects)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    repository: Mapped[Repository | None] = relationship(lazy="joined")


class Workflow(TimestampMixin, Base):
    """One user objective planned by the orchestrator into a task graph."""

    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    plan: Mapped[dict | None] = mapped_column(JSONB)
    thread_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    error: Mapped[str | None] = mapped_column(Text)
    # "workforce": one prompt handled by the Manager and the workers of a workforce (analysis, review, one report)
    kind: Mapped[str] = mapped_column(String(16), default="standard", server_default="standard", index=True)
    workforce_id: Mapped[int | None] = mapped_column(ForeignKey("workforces.id", ondelete="SET NULL"), index=True)
    review_policy: Mapped[str | None] = mapped_column(String(16))  # manager | human (None = human, as before)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (enum_check("status", TaskStatus),
                      CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
                      enum_check("lifecycle", TaskLifecycle),
                      CheckConstraint(lifecycle_pairs_sql(), name="lifecycle_matches_status"),
                      CheckConstraint("execution_mode IS NULL OR execution_mode IN ('AUTOMATIC','HUMAN_ASSISTED')",
                                      name="execution_mode_valid"))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id", ondelete="SET NULL"), index=True)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    task_type: Mapped[str] = mapped_column(String(32), default="general")  # architecture|frontend|backend|...
    required_skills: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.BACKLOG.value, index=True)
    # Detailed lifecycle; change it with app.workforce.lifecycle.move() so the transition is validated and recorded.
    lifecycle: Mapped[str] = mapped_column(String(24), default=TaskLifecycle.DRAFT.value,
                                           server_default=TaskLifecycle.DRAFT.value, index=True)
    objective: Mapped[str | None] = mapped_column(Text)           # why this task exists, in one or two sentences
    instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    expected_output: Mapped[str] = mapped_column(Text, default="", server_default="")
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)  # None = the worker's own timeout
    execution_mode: Mapped[str | None] = mapped_column(String(16))  # mode of the current/last attempt
    assignment_source: Mapped[str | None] = mapped_column(String(16))  # plan | manual | auto | redistributed
    accepted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(Integer, default=5)
    assigned_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"), index=True)
    acceptance_criteria: Mapped[list] = mapped_column(JSONB, default=list)
    budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    estimated_effort: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    review_status: Mapped[str] = mapped_column(String(16), default="none")  # none|pending|approved|revise|rejected
    # A critical operation (Approval Center catalogue) this task performs: acceptance then needs a human approval.
    critical_operation: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    branch: Mapped[str | None] = mapped_column(String(255))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def _loaded(self, name: str) -> str | None:
        """Current value of a column without being fooled by an expired attribute."""
        if name in self.__dict__:
            return self.__dict__[name]
        from sqlalchemy import inspect as sa_inspect
        state = sa_inspect(self)
        return getattr(self, name) if state.persistent else None

    @validates("status")
    def _status_implies_lifecycle(self, _key: str, value: str) -> str:
        """Code that only knows the coarse status keeps working: the lifecycle follows with the default detailed
        state. `lifecycle.move()` sets the lifecycle itself, so its more precise choice is kept."""
        if (not self.__dict__.get("_syncing") and value in DEFAULT_LIFECYCLE
                and COARSE_STATUS.get(self._loaded("lifecycle")) != value):
            self.__dict__["_syncing"] = True
            try:
                self.lifecycle = DEFAULT_LIFECYCLE[value]
            finally:
                self.__dict__.pop("_syncing", None)
        return value

    @validates("lifecycle")
    def _lifecycle_implies_status(self, _key: str, value: str) -> str:
        coarse = COARSE_STATUS.get(value)
        if not self.__dict__.get("_syncing") and coarse is not None and self._loaded("status") != coarse:
            self.__dict__["_syncing"] = True
            try:
                self.status = coarse
            finally:
                self.__dict__.pop("_syncing", None)
        return value


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (CheckConstraint("task_id <> depends_on_id", name="no_self_dependency"),)

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)


class PullRequest(TimestampMixin, Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    number: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(255))
    head_branch: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(32), default="open")
    checks: Mapped[dict | None] = mapped_column(JSONB)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Worktree(TimestampMixin, Base):
    """An agent's isolated git worktree + feature branch for one task (GH-03, spec Phase 12)."""

    __tablename__ = "worktrees"
    __table_args__ = (UniqueConstraint("project_id", "branch"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    branch: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255))
    base_commit: Mapped[str] = mapped_column(String(64))
    head_commit: Mapped[str | None] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|committed|merged|abandoned
    modified_files: Mapped[list] = mapped_column(JSONB, default=list)
    conflicts: Mapped[list] = mapped_column(JSONB, default=list)
    commits: Mapped[list] = mapped_column(JSONB, default=list)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
