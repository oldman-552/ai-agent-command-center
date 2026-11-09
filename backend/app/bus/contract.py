"""Structured message contract for the internal bus (spec Phase 6 "Structured Message Contract", MQ-02).

Every message on RabbitMQ is an `Envelope`. The AMQP properties mirror the envelope (message_id,
correlation_id, priority, timestamp, type, expiration) so the broker and tooling can act on them, but the
body is the authority. Consumers reject (dead-letter) anything that does not validate or whose
`schema_version` they do not understand — a poison message never loops.

This is platform-internal messaging. It is distinct from MCP (agent↔tool) and A2A (agent↔external agent).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1"})


class Category(StrEnum):
    """Event families required by Phase 6.1. A message type is `<category>.<verb>`."""

    TASK = "task"
    AGENT = "agent"
    EXECUTION = "execution"
    APPROVAL = "approval"
    WORKFLOW = "workflow"
    RECOVERY = "recovery"


class MessageType(StrEnum):
    # commands (work queues — exactly one consumer acts)
    EXECUTION_RUN = "execution.run"
    # events (fan-out — any number of subscribers)
    EXECUTION_STARTED = "execution.started"
    EXECUTION_FINISHED = "execution.finished"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    AGENT_STATE_CHANGED = "agent.state_changed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    WORKFLOW_UPDATED = "workflow.updated"
    RECOVERY_ORPHANS_TERMINATED = "recovery.orphans_terminated"
    RECOVERY_DEAD_LETTERED = "recovery.dead_lettered"


COMMANDS = frozenset({MessageType.EXECUTION_RUN})


def _now() -> datetime:
    return datetime.now(UTC)


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=36)
    schema_version: str = SCHEMA_VERSION
    message_type: MessageType
    sender: str = Field(max_length=128)        # "user:1", "agent:5", "orchestrator", "worker:<runtime>"
    recipient: str = Field(max_length=128)     # a specialty ("execution:api"), an agent, or "*" for events
    organization_id: int
    project_id: int | None = None
    task_id: int | None = None
    correlation_id: str = Field(max_length=64)  # the business flow this belongs to (e.g. the execution id)
    causation_id: str | None = Field(default=None, max_length=36)  # message that caused this one
    idempotency_key: str = Field(max_length=160)  # consumers process a key at most once
    priority: int = Field(default=5, ge=0, le=9)
    timestamp: datetime = Field(default_factory=_now)
    expires_at: datetime | None = None
    attempt: int = Field(default=1, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"unsupported schema_version {v!r}")
        return v

    @property
    def category(self) -> Category:
        return Category(self.message_type.split(".", 1)[0])

    @property
    def is_command(self) -> bool:
        return self.message_type in COMMANDS

    def expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and (now or _now()) >= self.expires_at

    def next_attempt(self) -> Envelope:
        return self.model_copy(update={"attempt": self.attempt + 1})

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def from_bytes(cls, body: bytes) -> Envelope:
        return cls.model_validate_json(body)


def execution_run(*, execution_id: str, organization_id: int, engine: str, sender: str, reason: str,
                  project_id: int | None = None, task_id: int | None = None, priority: int = 5,
                  idempotency_key: str | None = None) -> Envelope:
    """Command: run (or resume) an execution. Routed to the engine's specialty queue."""
    return Envelope(message_type=MessageType.EXECUTION_RUN, sender=sender, recipient=f"execution:{engine}",
                    organization_id=organization_id, project_id=project_id, task_id=task_id,
                    correlation_id=execution_id, priority=priority,
                    idempotency_key=idempotency_key or f"execution.run:{execution_id}:{reason}",
                    payload={"execution_id": execution_id, "engine": engine, "reason": reason})
