"""AI Workforce: a Manager, its workers, task history, immutable submissions, Manager steps and reports."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin, utcnow
from app.models._base import enum_check, new_uuid, org_fk
from app.models.work import ExecutionMode


class Workforce(TimestampMixin, Base):
    """One Manager and its workers. One per organization for now; the schema does not depend on that."""

    __tablename__ = "workforces"
    __table_args__ = (UniqueConstraint("organization_id", "name"),
                      CheckConstraint("review_policy IN ('manager','human')", name="review_policy_valid"),
                      CheckConstraint("max_revisions BETWEEN 0 AND 10", name="max_revisions_range"))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    name: Mapped[str] = mapped_column(String(128))
    manager_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    # manager: the Manager may accept a result (critical operations and escalations still go to a person)
    # human:   every result waits for a person's review
    review_policy: Mapped[str] = mapped_column(String(16), default="manager", server_default="manager")
    max_revisions: Mapped[int] = mapped_column(Integer, default=2, server_default="2")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class WorkforceWorker(TimestampMixin, Base):
    """A worker slot. Identity, prompt, model/session, timeout and retry policy live on its agent; this row adds the
    execution mode and what the platform knows about the Arena account behind it."""

    __tablename__ = "workforce_workers"
    __table_args__ = (UniqueConstraint("workforce_id", "slot"), UniqueConstraint("agent_id"),
                      enum_check("mode", ExecutionMode), CheckConstraint("slot >= 1", name="slot_positive"))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    workforce_id: Mapped[int] = mapped_column(ForeignKey("workforces.id", ondelete="CASCADE"), index=True)
    slot: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[int] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    mode: Mapped[str] = mapped_column(String(16), default=ExecutionMode.HUMAN_ASSISTED.value)
    # Sent to the bridge as the client conversation id; stable for the worker, never shared between workers.
    client_session_id: Mapped[str] = mapped_column(String(128))
    account_label: Mapped[str | None] = mapped_column(String(128))  # the owner's own name for the account
    # What the bridge's registry says about the account behind this worker, as last read:
    # {type: deterministic_session|deterministic_single_account|pooled|unknown, account, account_hash, attested: false…}
    account_binding: Mapped[dict | None] = mapped_column(JSONB)
    fallback_to_human: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    assignee_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    health: Mapped[dict | None] = mapped_column(JSONB)


class TaskTransition(Base):
    """Every lifecycle change of a task. Progress shown anywhere comes from tasks and these rows only."""

    __tablename__ = "task_transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = org_fk()
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24))
    actor: Mapped[str] = mapped_column(String(64), default="system")  # user:3 | agent:5 | manager | orchestrator
    reason: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TaskSubmission(Base):
    """One result of one attempt, exactly as it arrived. Never edited: a revision is a new version."""

    __tablename__ = "task_submissions"
    __table_args__ = (UniqueConstraint("task_id", "version"), UniqueConstraint("execution_id"),
                      CheckConstraint("mode IN ('AUTOMATIC','HUMAN_ASSISTED')", name="mode_valid"))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer)
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id", ondelete="SET NULL"))
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    worker_id: Mapped[int | None] = mapped_column(ForeignKey("workforce_workers.id", ondelete="SET NULL"))
    mode: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(32))        # arena_local_bridge | human | api | mock | …
    submitted_by: Mapped[str] = mapped_column(String(64))  # agent:5 | user:3
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSONB, default=list)
    provider: Mapped[dict] = mapped_column(JSONB, default=dict)  # kind, session, request id, account binding (as known)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    evaluation_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_results.id", ondelete="SET NULL"))
    review: Mapped[dict | None] = mapped_column(JSONB)      # the Manager's verdict, as validated
    decision: Mapped[str | None] = mapped_column(String(24))  # approved | revision_requested | rejected | escalated
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class TaskAttachment(Base):
    """A file a person attached to a Human-Assisted submission. Stored outside the web root; served only to members
    of the same organization."""

    __tablename__ = "task_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    organization_id: Mapped[int] = org_fk()
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("task_submissions.id", ondelete="SET NULL"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(1024))
    uploaded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ManagerStep(Base):
    """One piece of Manager work (analysis, plan, review, consolidation): what ran, on which model, how it ended."""

    __tablename__ = "workforce_manager_steps"
    __table_args__ = (CheckConstraint("kind IN ('analyze','plan','review','consolidate')", name="kind_valid"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    organization_id: Mapped[int] = org_fk()
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), index=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("task_submissions.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))  # ok | invalid | failed
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id", ondelete="SET NULL"))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)


class WorkforceReport(Base):
    """The consolidated result of an objective. Versioned: consolidating again after a change adds a version."""

    __tablename__ = "workforce_reports"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),
                      UniqueConstraint("workflow_id", "state_hash", name="uq_workforce_reports_state_hash"))

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = org_fk()
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))  # complete | partial | failed
    state_hash: Mapped[str] = mapped_column(String(64))  # of the task states and submissions it was built from
    content: Mapped[dict] = mapped_column(JSONB)
    markdown: Mapped[str] = mapped_column(Text, default="")
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    manager_step_id: Mapped[str | None] = mapped_column(ForeignKey("workforce_manager_steps.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------- lifecycle history safety net ----------------
# `app.workforce.lifecycle.move()` validates a transition and writes its history row. Code that only knows the coarse
# `status` (the standard orchestrator, a cancel route) changes the lifecycle through the model's validators instead;
# this hook writes the history row for those changes too, so the history of a task never has a gap.
def _record_direct_changes(session, _flush_context, _instances) -> None:
    from sqlalchemy import inspect as sa_inspect

    from app.models.work import Task

    for obj in list(session.dirty):
        if not isinstance(obj, Task):
            continue
        hist = sa_inspect(obj).attrs.lifecycle.history
        if not hist.has_changes() or not hist.added:
            continue
        new = hist.added[0]
        recorded = obj.__dict__.pop("_lifecycle_recorded", None)
        if recorded == new:
            continue  # written by lifecycle.move()
        old = recorded or (hist.deleted[0] if hist.deleted else None)
        if old == new:
            continue
        session.add(TaskTransition(organization_id=obj.organization_id, task_id=obj.id, from_state=old, to_state=new,
                                   actor="system", reason="status changed outside the lifecycle service",
                                   details={"direct": True}))


def _install_lifecycle_hook() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(Session, "before_flush", _record_direct_changes):
        event.listen(Session, "before_flush", _record_direct_changes)


_install_lifecycle_hook()
