"""Helpers for the AI Workforce tests.

Tier: mock + local integration. The Manager is the built-in mock provider (labelled, deterministic, not AI); Automatic
workers talk to the FAKE Arena Local Bridge over real HTTP. Nothing here is evidence of a real Claude or Arena run.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

import app.main  # noqa: F401  (registers engines, hooks and routes — as the API process does)
from app.models import (
    Agent,
    CircuitBreaker,
    Execution,
    Provider,
    Task,
    TaskSubmission,
    User,
    Workflow,
    Workforce,
    WorkforceWorker,
)
from app.runtime import service as runtime
from app.workforce import control, scheduler, service
from tests.fakebridge import KEY, session_id

BRIDGES = "/api/workforce/bridges"


def mock_provider(db) -> Provider:
    return db.scalar(select(Provider).where(Provider.organization_id == 1, Provider.kind == "mock"))


def admin(db) -> User:
    return db.scalar(select(User).where(User.username == "admin"))


def force_of(db) -> Workforce:
    return service.get(db, 1)


def worker(db, slot: int) -> WorkforceWorker:
    return db.scalar(select(WorkforceWorker).where(WorkforceWorker.workforce_id == force_of(db).id,
                                                   WorkforceWorker.slot == slot))


def agent_of(db, slot: int) -> Agent:
    return db.get(Agent, worker(db, slot).agent_id)


def reset(db, *, manager: str | None = "mock") -> Workforce:
    """The default workforce in a known state: Manager on the mock provider (or without a model), four
    Human-Assisted workers without a provider, nothing left open from another test."""
    db.rollback()
    force = force_of(db)
    for wf in db.scalars(select(Workflow).where(Workflow.organization_id == 1, Workflow.kind == "workforce",
                                                Workflow.status.in_(["planning", "running"]))):
        if wf.status == "planning":
            wf.status, wf.error = "cancelled", "test reset"
        else:
            control.cancel_objective(db, wf, admin(db), "test reset")
    db.commit()
    m = db.get(Agent, force.manager_agent_id)
    p = mock_provider(db)
    m.provider_id, m.model = (p.id, "mock-model") if manager == "mock" else (None, None)
    m.fallback_provider_id = m.fallback_model = None
    m.system_instructions, m.is_enabled, m.archived_at, m.timeout_seconds = "", True, None, 600
    force.review_policy, force.max_revisions = "manager", 2
    for w in service.workers(db, force.id):
        a = db.get(Agent, w.agent_id)
        spec = service.DEFAULT_WORKERS[w.slot - 1]
        a.provider_id = a.model = None
        a.is_enabled, a.archived_at, a.state, a.concurrency_limit = True, None, "IDLE", 1
        a.skills, a.role, a.timeout_seconds = list(spec.skills), spec.role, 900
        a.retry_policy = {"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1}
        w.mode, w.fallback_to_human, w.assignee_user_id, w.account_binding = "HUMAN_ASSISTED", True, None, None
    for cb in db.scalars(select(CircuitBreaker)):
        db.delete(cb)
    db.commit()
    return force


def objective(db, prompt: str, *, policy: str | None = None, user: User | None = None) -> Workflow:
    wf = control.submit_objective(db, force_of(db), user or admin(db), prompt=prompt, title=f"T {uuid.uuid4().hex[:6]}",
                                  review_policy=policy)
    db.commit()
    return wf


def planned(db, prompt: str, **kw) -> Workflow:
    wf = objective(db, prompt, **kw)
    assert asyncio.run(control.plan(wf.id)) == "planned", db.get(Workflow, wf.id).error
    db.expire_all()
    return db.get(Workflow, wf.id)


def tasks_of(db, wf) -> dict[str, Task]:
    db.expire_all()
    return {t.title.removeprefix("[MOCK] "): t for t in db.scalars(select(Task).where(Task.workflow_id == wf.id)
                                                                  .order_by(Task.id))}


def tick(wf) -> dict:
    return asyncio.run(control.tick(wf.id))


def run_executions(db, wf) -> list[str]:
    """What a worker process does: run each dispatched execution, then fold its outcome into the task."""
    db.expire_all()
    exs = db.scalars(select(Execution).join(Task, Task.id == Execution.task_id).where(
        Task.workflow_id == wf.id, Execution.status == "CREATED")).all()
    out = []
    for ex in exs:
        asyncio.run(runtime.run_execution(ex.id))
        out.append(asyncio.run(scheduler.on_execution_finished(db, ex.id)))
    return out


def drive(db, wf, rounds: int = 12) -> dict:
    """Tick and run executions until nothing moves any more."""
    summary: dict = {}
    for _ in range(rounds):
        summary = tick(wf)
        ran = run_executions(db, wf)
        db.expire_all()
        pending = scheduler.pending_reviews(db, wf.id)
        if not ran and not pending and not summary.get("dispatched") and not summary.get("reviewed"):
            break
    db.expire_all()
    return summary


def subs(db, task: Task) -> list[TaskSubmission]:
    db.expire_all()
    return list(db.scalars(select(TaskSubmission).where(TaskSubmission.task_id == task.id)
                           .order_by(TaskSubmission.version)))


def states(db, wf) -> dict[str, str]:
    return {k: t.lifecycle for k, t in tasks_of(db, wf).items()}


# ---------------- Automatic workers on the FAKE bridge ----------------
def bridge(admin_client, fake_bridge, **settings) -> dict:
    r = admin_client.post(BRIDGES, json={"name": f"Bridge {uuid.uuid4().hex[:8]}", "base_url": fake_bridge.url,
                                         "api_key": KEY, **({"settings": settings} if settings else {})})
    assert r.status_code == 201, r.text
    b = r.json()
    r = admin_client.post(f"{BRIDGES}/{b['id']}/terms", json={"statement": b["terms"]["statement"]})
    assert r.status_code == 200, r.text
    return r.json()


def automatic(db, slot: int, provider_id: int, model: str | None = None, **kw) -> WorkforceWorker:
    w = worker(db, slot)
    service.update_worker(db, w, user=admin(db), patch={"provider_id": provider_id, "mode": "AUTOMATIC",
                                                        "model": model or session_id(slot), **kw})
    db.commit()
    return w


def all_automatic(db, admin_client, fake_bridge, **settings) -> dict:
    b = bridge(admin_client, fake_bridge, **settings)
    for slot in (1, 2, 3, 4):
        automatic(db, slot, b["id"])
    return b


def second_person(make_user, username: str = "wf_second_person", role: str = "ORGANIZATION_ADMIN"):
    """Another signed-in person who may decide approvals: the author of a critical result cannot approve it."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import login_as

    make_user(username, role)
    return login_as(TestClient(app), username, "user-password-123")


def task(db, task_id: int) -> Task:
    """The task as it is in the database now."""
    db.expire_all()
    return db.get(Task, task_id)
