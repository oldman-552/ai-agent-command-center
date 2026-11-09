"""Agent lifecycle state machine (spec Phase 3). Every transition is recorded."""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import Agent, AgentStateTransition
from app.models import AgentState as S

ALLOWED: dict[S, set[S]] = {
    S.CREATED: {S.INITIALIZING, S.DISABLED},
    S.INITIALIZING: {S.IDLE, S.FAILED, S.DISABLED},
    S.IDLE: {S.ASSIGNED, S.DISABLED, S.INITIALIZING},
    S.ASSIGNED: {S.RUNNING, S.IDLE, S.CANCELLED, S.BLOCKED, S.DISABLED},
    S.RUNNING: {S.REVIEWING, S.COMPLETED, S.WAITING_FOR_INPUT, S.WAITING_FOR_APPROVAL, S.BLOCKED, S.FAILED,
                S.CANCELLED},
    S.REVIEWING: {S.COMPLETED, S.RUNNING, S.FAILED, S.CANCELLED},
    S.WAITING_FOR_INPUT: {S.RUNNING, S.CANCELLED, S.FAILED},
    S.WAITING_FOR_APPROVAL: {S.RUNNING, S.CANCELLED, S.FAILED},
    S.BLOCKED: {S.ASSIGNED, S.RUNNING, S.CANCELLED, S.FAILED, S.IDLE},
    S.COMPLETED: {S.IDLE, S.DISABLED},
    S.FAILED: {S.IDLE, S.INITIALIZING, S.DISABLED},
    S.CANCELLED: {S.IDLE, S.DISABLED},
    S.DISABLED: {S.INITIALIZING},
}
BUSY = {S.ASSIGNED, S.RUNNING, S.REVIEWING, S.WAITING_FOR_INPUT, S.WAITING_FOR_APPROVAL, S.BLOCKED}


def transition(db: Session, agent: Agent, to: S, *, actor: str, reason: str = "") -> None:
    cur = S(agent.state)
    if to == cur:
        return
    if to not in ALLOWED[cur]:
        raise HTTPException(status.HTTP_409_CONFLICT, f"illegal_transition:{cur.value}->{to.value}")
    db.add(AgentStateTransition(agent_id=agent.id, from_state=cur.value, to_state=to.value, actor=actor, reason=reason))
    agent.state = to.value
