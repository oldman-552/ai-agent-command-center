"""Universal Agent Runtime contract (spec Phase 2, ADR-002).

Every execution method (API, CLI, Browser Bridge, A2A) implements `ExecutionEngine`. The runtime owns the
lifecycle (state machine, deadlines, cancellation, checkpoints, tool gate, retries, telemetry); engines only
produce events. Nothing here imports a provider SDK or LangGraph.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models import ExecutionStatus as X

# Allowed execution state transitions (13 states).
TRANSITIONS: dict[X, set[X]] = {
    X.CREATED: {X.INITIALIZING, X.CANCELLED},
    X.INITIALIZING: {X.READY, X.FAILED, X.CANCELLED},
    X.READY: {X.RUNNING, X.CANCELLED},
    X.RUNNING: {X.WAITING_FOR_TOOL, X.WAITING_FOR_APPROVAL, X.WAITING_FOR_INPUT, X.CHECKPOINTED, X.COMPLETED,
                X.FAILED, X.CANCELLED, X.TERMINATED},
    X.WAITING_FOR_TOOL: {X.RUNNING, X.FAILED, X.CANCELLED, X.TERMINATED},
    X.WAITING_FOR_APPROVAL: {X.RUNNING, X.FAILED, X.CANCELLED},
    X.WAITING_FOR_INPUT: {X.RUNNING, X.FAILED, X.CANCELLED},
    X.CHECKPOINTED: {X.RUNNING, X.RECOVERING, X.CANCELLED, X.FAILED, X.TERMINATED},
    X.RECOVERING: {X.RUNNING, X.FAILED, X.CANCELLED},
    X.FAILED: {X.RECOVERING, X.CANCELLED},  # retry/resume from checkpoint, or give up
    X.COMPLETED: set(),
    X.CANCELLED: set(),
    X.TERMINATED: {X.RECOVERING, X.CANCELLED},  # e.g. worker died; resume from last checkpoint
}
TERMINAL = {X.COMPLETED, X.CANCELLED}


class ToolDenied(Exception):
    """Raised when an engine requests a tool outside the execution's permissions."""


class RetryableError(Exception):
    """Transient failure (rate limit, network, provider 5xx). The runtime may retry with backoff."""


@dataclass
class EngineEvent:
    type: str  # output_delta | log | tool_call | tool_result | usage | message
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class EngineContext:
    """What an engine may see and do. Credentials are never placed here; adapters fetch them server-side."""

    execution_id: str
    agent: dict[str, Any]          # agent config snapshot (no secrets)
    input: dict[str, Any]          # assembled context (trust-segmented in M11)
    tool_permissions: list[str]
    resume_state: dict[str, Any] | None
    checkpoint: Callable[[dict[str, Any]], Awaitable[None]]
    call_tool: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    is_cancelled: Callable[[], Awaitable[bool]]


class ExecutionEngine(Protocol):
    kind: str
    is_mock: bool

    def run(self, ctx: EngineContext) -> AsyncIterator[EngineEvent]:
        """Yield events; return normally on success; raise on failure."""
        ...


_ENGINES: dict[str, Callable[[], ExecutionEngine]] = {}


def register_engine(kind: str, factory: Callable[[], ExecutionEngine]) -> None:
    _ENGINES[kind] = factory


def get_engine(kind: str) -> ExecutionEngine:
    if kind not in _ENGINES:
        raise KeyError(f"no execution engine registered for '{kind}'")
    return _ENGINES[kind]()


def registered_engines() -> list[str]:
    return sorted(_ENGINES)
