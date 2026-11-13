"""WF-13 — failure recovery: provider unavailable or switched off, timeout, invalid response, rate limit, and
interrupted worker / queue / database. Every failure is classified, retries are bounded, and the task is left in a
state from which it can go on (a new attempt, a person, or a visible FAILED that a person can reopen).

Tier: mock (mock Manager; MOCK provider for workers, its failures injected with `h.worker_provider`). The policy
table still names the bridge's codes: `failure_action` keeps classifying them.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.bus import handlers
from app.bus.contract import MessageType, workflow_command
from app.models import Execution, ExecutionEvent, OutboxMessage, Task, Workflow, WorkforceReport
from app.orchestration.scheduler import recover_running
from app.workforce import control, manager, scheduler
from app.workforce.scheduler import failure_action
from tests import wf as h

API = "/api/workforce"


@pytest.fixture()
def force(db):
    return h.reset(db)


@pytest.fixture()
def auto(db, force):
    h.all_automatic(db)


def executions(db, task: Task) -> list[Execution]:
    db.expire_all()
    return list(db.scalars(select(Execution).where(Execution.task_id == task.id).order_by(Execution.created_at)))


def one(db, text: str = "1. Summarise the options [research]"):
    wf = h.planned(db, text)
    return wf, next(iter(h.tasks_of(db, wf).values()))


# ---------------- the policy, as a table ----------------
@pytest.mark.parametrize("code,category,failures,fallback,expected", [
    ("outcome_unknown", "outcome_unknown", 1, True, "human"),   # never an automatic resend
    ("outcome_unknown", "outcome_unknown", 1, False, "fail"),
    ("session_account_unavailable", "account_unavailable", 1, True, "human"),
    ("credentials_unreadable", "account_unavailable", 1, False, "fail"),
    ("no_active_session", "session_unavailable", 1, True, "human"),
    ("invalid_key", "configuration", 1, True, "human"),
    ("terms_not_acknowledged", "configuration", 1, False, "fail"),
    ("unreachable", "bridge_unavailable", 1, True, "retry"),
    ("unreachable", "bridge_unavailable", 3, True, "human"),
    ("unreachable", "bridge_unavailable", 3, False, "fail"),
    ("rate_limited", "rate_limit", 2, False, "retry"),
    ("queue_full", "rate_limit", 3, True, "human"),
    ("upstream_error", "upstream", 1, False, "retry"),
    ("empty_response", "invalid_response", 2, True, "retry"),
    ("unexpected_tool_call", "invalid_response", 3, False, "fail"),
    ("timeout", "timeout", 1, True, "retry"),
    ("timeout", "timeout", 3, True, "human"),
    ("policy", "policy", 1, True, "fail"),
    ("error", "error", 1, True, "fail"),
])
def test_every_failure_class_has_one_bounded_answer(code, category, failures, fallback, expected):
    info = {"code": code, "category": category}
    assert failure_action(info, failures, 3, fallback) == expected
    assert failure_action(info, 99, 3, fallback) in ("human", "fail")  # never retried without end


# ---------------- provider unavailable ----------------
@pytest.mark.parametrize("fallback,final", [(True, "WAITING_FOR_ACCEPTANCE"), (False, "FAILED")])
def test_an_unreachable_provider_is_retried_a_bounded_number_of_times(admin_client, db, auto, monkeypatch,
                                                                      fallback, final):
    admin_client.patch(f"{API}/workers/{h.worker(db, 1).id}", json={"fallback_to_human": fallback})
    wf, t = one(db)
    h.worker_provider(monkeypatch, fail=99)  # the provider goes away after the plan was made
    h.drive(db, wf)
    task = h.task(db, t.id)
    runs = executions(db, t)
    assert len(runs) == 3 == task.max_attempts and {e.status for e in runs} == {"FAILED"}  # bounded
    assert [e.idempotency_key for e in runs] == [f"wf-task:{t.id}:attempt:{i}" for i in (1, 2, 3)]
    fails = task.result["failures"]
    assert {f["category"] for f in fails} == {"bridge_unavailable"}  # (the transient class, whatever the provider)
    assert [f["action"] for f in fails] == ["retry", "retry", "human" if fallback else "fail"]
    assert task.lifecycle == final and task.result["auto_failures"] == 3
    card = admin_client.get(f"{API}/tasks/{t.id}").json()
    if fallback:
        assert card["mode"] == "HUMAN_ASSISTED" and card["fallback"]["reason"] == fails[-1]["code"]
        # the person finishes the task; the objective completes without the provider
        admin_client.post(f"{API}/tasks/{t.id}/accept")
        admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": "done by hand while the provider was down"})
        assert h.drive(db, wf)["status"] == "completed"
    else:
        assert card["error"].startswith(fails[-1]["code"]) and h.tick(wf)["status"] == "failed"
    assert len(executions(db, t)) == 3  # and no further attempt afterwards


def test_a_switched_off_provider_sends_new_tasks_straight_to_a_person(admin_client, db, auto):
    wf, t = one(db)  # planned while the provider works (the Manager uses it too)
    mock = h.mock_provider(db)
    mock.enabled = False
    db.commit()
    try:
        out = h.tick(wf)
        task = h.task(db, t.id)
        assert out["offered"] == [t.id] and out["dispatched"] == []
        assert task.lifecycle == "WAITING_FOR_ACCEPTANCE" and task.execution_mode == "HUMAN_ASSISTED"
        assert task.result["fallback"]["reason"] == "provider_disabled" and executions(db, t) == []
    finally:
        db.rollback()
        h.mock_provider(db).enabled = True
        db.commit()


# ---------------- timeout and invalid responses ----------------
def test_a_task_timeout_is_enforced_and_bounded(admin_client, db, auto, monkeypatch):
    h.worker_provider(monkeypatch, slow=20)
    wf, t = one(db, "1. Think for a long time [research]")
    t.timeout_seconds, t.max_attempts = 1, 2
    db.commit()
    h.drive(db, wf)
    task = h.task(db, t.id)
    runs = executions(db, t)
    assert len(runs) == 2 and all(e.error_category == "timeout" for e in runs)
    assert all((e.ended_at - e.started_at).total_seconds() < 6 for e in runs)  # the task's limit, not the worker's
    assert [f["code"] for f in task.result["failures"]] == ["timeout", "timeout"]
    assert [f["action"] for f in task.result["failures"]] == ["retry", "human"]
    assert task.lifecycle == "WAITING_FOR_ACCEPTANCE"
    # the request may have reached the provider: the person is told before sending it again
    assert "may have reached the worker's provider" in task.result["fallback"]["note"]


def test_an_empty_response_is_never_a_result(admin_client, db, auto, monkeypatch):
    h.worker_provider(monkeypatch, empty=True)
    wf, t = one(db, "1. Answer strangely [research]")
    t.max_attempts = 2
    db.commit()
    h.drive(db, wf)
    task = h.task(db, t.id)
    assert h.subs(db, t) == [] and len(executions(db, t)) == 2
    assert [f["code"] for f in task.result["failures"]] == ["empty_response", "empty_response"]
    assert {f["category"] for f in task.result["failures"]} == {"invalid_response"}
    assert task.lifecycle == "WAITING_FOR_ACCEPTANCE"


def test_a_rate_limit_is_waited_out_inside_the_attempt(admin_client, db, auto, monkeypatch):
    h.agent_of(db, 1).retry_policy = {"max_attempts": 2, "backoff_seconds": 0, "backoff_multiplier": 1}
    db.commit()
    seen = h.worker_provider(monkeypatch, fail=1)
    wf, t = one(db)
    h.drive(db, wf)
    task = h.task(db, t.id)
    runs = executions(db, t)
    assert task.lifecycle == "COMPLETED" and len(runs) == 1 and runs[0].attempt == 2  # one task attempt
    retry = db.scalar(select(ExecutionEvent).where(ExecutionEvent.execution_id == runs[0].id,
                                                   ExecutionEvent.type == "retry"))
    assert retry is not None and retry.payload["attempt"] == 2
    assert "failures" not in task.result and len(seen) == 2


def test_repeated_failures_open_the_workers_circuit_and_new_tasks_go_to_a_person(admin_client, db, auto,
                                                                                 monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "circuit_failure_threshold", 2)
    h.worker_provider(monkeypatch, fail=99)
    wf, t = one(db)
    h.drive(db, wf)
    task = h.task(db, t.id)
    # two failed attempts opened the circuit; the third never started — the task went to a person instead
    assert len(executions(db, t)) == 2 and task.lifecycle == "WAITING_FOR_ACCEPTANCE"
    assert task.result["fallback"]["reason"] == "circuit_open"
    assert admin_client.get(API).json()["workforce"]["workers"][0]["status"] == {
        "state": "fallback", "reason": "circuit_open"}


# ---------------- worker, queue and database interruptions ----------------
def test_a_lost_message_is_made_up_for_when_a_worker_starts(db, force):
    wf = h.planned(db, "1. First [research]")
    planning = h.objective(db, "1. Second [research]")
    before = db.scalar(select(func.max(OutboxMessage.id))) or 0
    assert recover_running(db) >= 2
    kinds = {(m.envelope["payload"]["workflow_id"], m.envelope["message_type"]) for m in db.scalars(
        select(OutboxMessage).where(OutboxMessage.id > before))}
    assert (wf.id, "workflow.advance") in kinds and (planning.id, "workflow.plan") in kinds


async def test_the_bus_commands_drive_a_workforce_objective(db, force):
    def cmd(type_, wf, attempt=1):
        return workflow_command(type_, workflow_id=wf.id, organization_id=1, project_id=wf.project_id,
                                sender="test").model_copy(update={"attempt": attempt})

    wf = h.objective(db, "1. First [research]")
    await handlers.handle_orchestrator_command(cmd(MessageType.WORKFLOW_PLAN, wf))
    db.expire_all()
    assert db.get(Workflow, wf.id).status == "running"
    await handlers.handle_orchestrator_command(cmd(MessageType.WORKFLOW_PLAN, wf))  # redelivered: nothing happens
    await handlers.handle_orchestrator_command(cmd(MessageType.WORKFLOW_ADVANCE, wf))
    db.expire_all()
    t = db.scalar(select(Task).where(Task.workflow_id == wf.id))
    assert t.lifecycle == "WAITING_FOR_ACCEPTANCE"
    await handlers.handle_orchestrator_command(cmd(MessageType.WORKFLOW_ADVANCE, wf))  # idempotent
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Task).where(Task.workflow_id == wf.id)) == 1

    # the Manager's model is unreachable: the command is retried by the bus, and the last delivery says so
    stuck = h.objective(db, "[[mock:unavailable]] 1. Task [research]")
    with pytest.raises(manager.ManagerUnavailable):
        await handlers.handle_orchestrator_command(cmd(MessageType.WORKFLOW_PLAN, stuck, attempt=1))
    db.expire_all()
    assert db.get(Workflow, stuck.id).status == "planning"
    await handlers.handle_orchestrator_command(cmd(MessageType.WORKFLOW_PLAN, stuck, attempt=5))
    db.expire_all()
    assert db.get(Workflow, stuck.id).status == "failed" and "could not be reached" in db.get(Workflow, stuck.id).error


def test_execution_finished_events_reach_the_right_scheduler(db, admin_client, auto):
    from app.bus.contract import Envelope
    from app.runtime import service as runtime
    wf = h.planned(db, "1. First [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    ex = executions(db, t)[0]

    async def deliver():
        await runtime.run_execution(ex.id)
        env = Envelope(message_type=MessageType.EXECUTION_FINISHED, sender="worker:test", recipient="*",
                       organization_id=1, correlation_id=ex.id, idempotency_key=f"t:{ex.id}",
                       payload={"execution_id": ex.id})
        await handlers.handle_execution_finished(env)
        await handlers.handle_execution_finished(env)  # at-least-once delivery

    asyncio.run(deliver())
    assert h.task(db, t.id).lifecycle == "SUBMITTED" and len(h.subs(db, t)) == 1


def test_a_round_that_breaks_half_way_leaves_nothing_half_done(db, force, monkeypatch):
    wf = h.planned(db, "1. First [research]\n2. Second [engineering]\n3. Third [qa]")
    real = scheduler._offer_human
    calls = []

    def failing(db_, t, *a, **kw):
        calls.append(t.id)
        if len(calls) == 2:
            raise RuntimeError("database connection lost")
        return real(db_, t, *a, **kw)

    monkeypatch.setattr(scheduler, "_offer_human", failing)
    with pytest.raises(RuntimeError, match="connection lost"):
        h.tick(wf)
    db.rollback()
    assert set(h.states(db, wf).values()) == {"PLANNED"}  # the whole round was undone, including the first offer
    assert all(t.attempts == 0 for t in h.tasks_of(db, wf).values())
    monkeypatch.setattr(scheduler, "_offer_human", real)
    assert len(h.tick(wf)["offered"]) == 3
    assert set(h.states(db, wf).values()) == {"WAITING_FOR_ACCEPTANCE"}


def test_reopening_a_failed_task_reopens_the_objective_and_adds_a_report_version(admin_client, db, auto,
                                                                                 monkeypatch):
    admin_client.patch(f"{API}/workers/{h.worker(db, 1).id}", json={"fallback_to_human": False})
    h.worker_provider(monkeypatch, empty=True)
    wf, t = one(db, "1. Answer strangely [research]")
    t.max_attempts = 1
    db.commit()
    out = h.drive(db, wf)
    assert out["status"] == "failed" and out["report_version"] == 1 and h.task(db, t.id).lifecycle == "FAILED"
    # a person changes the mode and gives the task another chance
    admin_client.patch(f"{API}/workers/{h.worker(db, 1).id}", json={"mode": "HUMAN_ASSISTED"})
    r = admin_client.post(f"{API}/tasks/{t.id}/reopen", json={"note": "I will do it by hand"})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Workflow, wf.id).status == "running"
    h.tick(wf)
    admin_client.post(f"{API}/tasks/{t.id}/accept")
    admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": "the answer, by hand"})
    out = h.drive(db, wf)
    assert out["status"] == "completed" and out["report_version"] == 2
    versions = db.scalars(select(WorkforceReport.status).where(WorkforceReport.workflow_id == wf.id)
                          .order_by(WorkforceReport.version)).all()
    assert versions == ["failed", "complete"]
    assert asyncio.run(control.tick(wf.id))["report_version"] == 2  # same state, same report: no version 3
