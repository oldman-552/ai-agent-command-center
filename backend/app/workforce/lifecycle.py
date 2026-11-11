"""The task lifecycle of the AI Workforce: which changes are allowed, and the history of every change (WF-07).

`TaskLifecycle` is the detailed state; `TaskStatus` is the scheduler's coarse phase and always follows it (model
validators + a table constraint). Every change goes through `move()`: it refuses a transition that is not in
`ALLOWED` and writes a `TaskTransition` row with who did it and why. Progress shown anywhere is read from tasks and
these rows — never from a model's text.

    DRAFT ─► PLANNED ─► READY ─► ASSIGNED ─┬─► QUEUED ─► RUNNING ──────────────┐            (Automatic)
                 │        ▲                └─► WAITING_FOR_ACCEPTANCE ─► ACCEPTED ┤            (Human-Assisted)
                 ▼        │                                                       ▼
              BLOCKED ────┘                         REVISION_REQUESTED ◄── UNDER_REVIEW ◄── SUBMITTED
                                                            │                 │    │
                                                            └─► ASSIGNED      │    └─► REJECTED
                                                                              ▼
                                                                          APPROVED ─► COMPLETED
    FAILED / REJECTED ─► READY only when a person reopens the task. CANCELLED and COMPLETED are final.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Task, TaskTransition
from app.models import TaskLifecycle as L

_A = {
    L.DRAFT: {L.PLANNED, L.CANCELLED},
    L.PLANNED: {L.READY, L.BLOCKED, L.CANCELLED},
    L.BLOCKED: {L.PLANNED, L.READY, L.CANCELLED},
    L.READY: {L.ASSIGNED, L.BLOCKED, L.CANCELLED},
    L.ASSIGNED: {L.QUEUED, L.WAITING_FOR_ACCEPTANCE, L.READY, L.CANCELLED},
    L.WAITING_FOR_ACCEPTANCE: {L.ACCEPTED, L.READY, L.CANCELLED},
    L.ACCEPTED: {L.SUBMITTED, L.WAITING_FOR_ACCEPTANCE, L.READY, L.CANCELLED},
    # an Automatic attempt that cannot go on: try again (READY), hand to a person, or give up
    L.QUEUED: {L.RUNNING, L.READY, L.WAITING_FOR_ACCEPTANCE, L.FAILED, L.CANCELLED},
    L.RUNNING: {L.SUBMITTED, L.READY, L.WAITING_FOR_ACCEPTANCE, L.FAILED, L.CANCELLED},
    L.SUBMITTED: {L.UNDER_REVIEW, L.CANCELLED},
    L.UNDER_REVIEW: {L.APPROVED, L.REVISION_REQUESTED, L.REJECTED, L.CANCELLED},
    # APPROVED: the review passed; COMPLETED follows at once, or after a required human approval
    L.APPROVED: {L.COMPLETED, L.REVISION_REQUESTED, L.REJECTED, L.CANCELLED},
    L.REVISION_REQUESTED: {L.ASSIGNED, L.BLOCKED, L.CANCELLED},
    L.REJECTED: {L.READY},
    L.FAILED: {L.READY},
    L.COMPLETED: set(),
    L.CANCELLED: set(),
}
ALLOWED: dict[str, frozenset[str]] = {k.value: frozenset(v.value for v in vs) for k, vs in _A.items()}

FINAL = frozenset({L.COMPLETED.value, L.CANCELLED.value})
# Nothing more happens to these without a person: the objective can be consolidated around them.
SETTLED = frozenset({L.COMPLETED.value, L.CANCELLED.value, L.REJECTED.value, L.FAILED.value})
ENDED = SETTLED  # states that carry a `finished_at`
# A worker is busy with these (they count against its concurrency).
WITH_WORKER = frozenset({L.ASSIGNED.value, L.WAITING_FOR_ACCEPTANCE.value, L.ACCEPTED.value, L.QUEUED.value,
                         L.RUNNING.value})
# Only a person may make these moves (reopening settled work).
HUMAN_ONLY_MOVES = frozenset({(L.REJECTED.value, L.READY.value), (L.FAILED.value, L.READY.value)})


class IllegalTransition(ValueError):
    def __init__(self, task_id: int | None, from_state: str, to_state: str, why: str = ""):
        super().__init__(f"illegal_transition:{from_state}->{to_state}" + (f" ({why})" if why else ""))
        self.task_id, self.from_state, self.to_state = task_id, from_state, to_state


def can_move(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED.get(from_state, frozenset())


def move(db: Session, task: Task, to: L | str, *, actor: str, reason: str = "",
         details: dict[str, Any] | None = None) -> TaskTransition | None:
    """Change the lifecycle of `task` and record it. Returns None when the task is already in that state.
    Does not commit."""
    to_state = to.value if isinstance(to, L) else str(to)
    cur = task.lifecycle
    if cur == to_state:
        return None
    if not can_move(cur, to_state):
        raise IllegalTransition(task.id, cur, to_state)
    if (cur, to_state) in HUMAN_ONLY_MOVES and not actor.startswith("user:"):
        raise IllegalTransition(task.id, cur, to_state, "only a person may reopen a settled task")
    now = datetime.now(UTC)
    task.lifecycle = to_state  # the coarse status follows (model validator)
    task.__dict__["_lifecycle_recorded"] = to_state
    if to_state == L.ASSIGNED.value and task.started_at is None:
        task.started_at = now
    if to_state in ENDED:
        task.finished_at = now
    elif cur in ENDED:
        task.finished_at = None
    row = TaskTransition(organization_id=task.organization_id, task_id=task.id, from_state=cur, to_state=to_state,
                         actor=actor[:64], reason=reason[:2000], details=details or {}, ts=now)
    db.add(row)
    from app.observability.live import notify
    notify(db, task.organization_id, "workforce", task_id=task.id, workflow_id=task.workflow_id, event="task")
    return row


def record_created(db: Session, task: Task, *, actor: str, reason: str = "", details: dict[str, Any] | None = None
                   ) -> TaskTransition:
    """The first history row of a task (it has no previous state)."""
    row = TaskTransition(organization_id=task.organization_id, task_id=task.id, from_state=None,
                         to_state=task.lifecycle, actor=actor[:64], reason=reason[:2000], details=details or {})
    db.add(row)
    return row


def history(db: Session, task_id: int) -> list[TaskTransition]:
    return list(db.scalars(select(TaskTransition).where(TaskTransition.task_id == task_id)
                           .order_by(TaskTransition.ts, TaskTransition.id)))


def transition_out(t: TaskTransition) -> dict[str, Any]:
    return {"id": t.id, "from": t.from_state, "to": t.to_state, "actor": t.actor, "reason": t.reason,
            "details": t.details, "at": t.ts}
