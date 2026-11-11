"""WF-08 — Automatic execution through the bridge: each worker in its own session, a stable idempotency key, and no
duplicate turn after a restart (`outcome_unknown` instead of a blind resend once the replay window is over).

Tier: local integration — the FAKE Arena Local Bridge over real HTTP and SSE, real PostgreSQL. The fake reproduces
the bridge's documented behaviour; a real bridge and real Arena accounts were NOT used (WF-20).
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.connectivity.arena_bridge.adapter import RULES_HEADER, TASK_HEADER
from app.models import Checkpoint, Execution, ExecutionEvent, Task, TaskSubmission, UsageRecord
from app.runtime import service as runtime
from app.workforce import scheduler
from tests import wf as h
from tests.fakebridge import ACCOUNTS, session_id

API = "/api/workforce"
FOUR = """Deliver:
1. Research part [research]
2. Build part [engineering]
3. Test part [qa]
4. Write part [documentation]"""


@pytest.fixture()
def force(db):
    return h.reset(db)


@pytest.fixture()
def auto(db, admin_client, fake_bridge, force):
    b = h.all_automatic(db, admin_client, fake_bridge)
    assert admin_client.post(f"{API}/bridges/{b['id']}/health").status_code == 200
    return b


def execution_of(db, task: Task) -> Execution:
    db.expire_all()
    return db.scalar(select(Execution).where(Execution.task_id == task.id).order_by(Execution.created_at.desc()))


def test_an_automatic_task_reaches_the_workers_session_and_comes_back_as_a_submission(admin_client, db, auto,
                                                                                      fake_bridge):
    wf = h.planned(db, "1. Summarise the options [research]")
    out = h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    ex = execution_of(db, t)
    assert out["dispatched"] == [t.id] and t.lifecycle == "QUEUED" and t.execution_mode == "AUTOMATIC"
    assert ex.status == "CREATED" and ex.idempotency_key == f"wf-task:{t.id}:attempt:1" and ex.engine == "api"
    assert ex.input_context["workforce"] == {"workflow_id": wf.id, "worker_id": h.worker(db, 1).id, "slot": 1,
                                             "attempt": 1}
    assert fake_bridge.completions() == [c for c in fake_bridge.completions() if c["model"] == "ping"]  # not sent yet

    assert h.run_executions(db, wf) == ["submitted"]
    sent = [c for c in fake_bridge.completions() if c["model"] != "ping"]
    assert len(sent) == 1 and sent[0]["model"] == session_id(1)
    assert sent[0]["session"] == h.worker(db, 1).client_session_id  # the worker's own conversation id
    assert sent[0]["idempotency"] == f"acc-{ex.id}-r0"
    delivered = fake_bridge.turns(session_id(1))
    assert len(delivered) == 1
    prompt = delivered[0]
    assert prompt.startswith(RULES_HEADER) and "You are one worker in a team" in prompt and TASK_HEADER in prompt
    assert "TASK: [MOCK] Summarise the options" in prompt and "EXPECTED OUTPUT:" in prompt
    assert "ACCEPTANCE CRITERIA:\n- [MOCK] The result addresses: Summarise the options" in prompt

    ex = execution_of(db, t)
    sub = h.subs(db, t)[0]
    assert ex.status == "COMPLETED" and ex.provider == "arena_local_bridge" and ex.is_mock is True
    assert ex.input_tokens is None and ex.output_tokens is None and ex.cost_usd is None  # not reported ≠ zero
    usage = db.scalar(select(UsageRecord).where(UsageRecord.execution_id == ex.id))
    assert usage.usage_known is False and usage.provider_request_id == ex.provider_request_id
    assert (sub.version, sub.mode, sub.source, sub.execution_id) == (1, "AUTOMATIC", "arena_local_bridge", ex.id)
    assert sub.submitted_by == f"agent:{h.worker(db, 1).agent_id}" and sub.is_mock is True
    assert sub.content == ex.output["text"] and sub.content.startswith("[FAKE ARENA BRIDGE — not Arena]")
    p = sub.provider
    assert p["kind"] == "arena_local_bridge" and p["model"] == session_id(1) and p["request_id"]
    assert p["client_session_id"] == h.worker(db, 1).client_session_id and p["usage_reported"] is False
    assert p["account_binding"]["type"] == "deterministic_session" and p["account_binding"]["attested"] is False
    assert not any(a in str(p) for a in ACCOUNTS)

    assert list(h.tick(wf)["reviewed"].values()) == ["approve"]
    trail = [(x["from"], x["to"]) for x in admin_client.get(f"{API}/tasks/{t.id}").json()["transitions"]]
    assert trail == [(None, "PLANNED"), ("PLANNED", "READY"), ("READY", "ASSIGNED"), ("ASSIGNED", "QUEUED"),
                     ("QUEUED", "RUNNING"), ("RUNNING", "SUBMITTED"), ("SUBMITTED", "UNDER_REVIEW"),
                     ("UNDER_REVIEW", "APPROVED"), ("APPROVED", "COMPLETED")]


def test_four_workers_use_four_sessions_and_never_each_others(admin_client, db, auto, fake_bridge):
    wf = h.planned(db, FOUR)
    out = h.drive(db, wf)
    assert out["status"] == "completed" and set(h.states(db, wf).values()) == {"COMPLETED"}
    sent = [c for c in fake_bridge.completions() if c["model"] != "ping"]
    assert sorted(c["model"] for c in sent) == [session_id(i) for i in (1, 2, 3, 4)]
    by_session = {c["model"]: c["session"] for c in sent}
    assert by_session == {session_id(i): h.worker(db, i).client_session_id for i in (1, 2, 3, 4)}
    assert len(set(by_session.values())) == 4
    for i, part in enumerate(("Research", "Build", "Test", "Write"), start=1):
        turns = fake_bridge.turns(session_id(i))
        assert len(turns) == 1 and f"[MOCK] {part} part" in turns[0]  # one turn, its own task, nobody else's
        assert sum(f"{other} part" in turns[0] for other in ("Research", "Build", "Test", "Write")) == 1
    # the fake counts turns per account: each of its four accounts answered exactly once
    assert fake_bridge.bridge.account_turns == dict.fromkeys(ACCOUNTS, 1)
    # the platform's slot: one request at a time per bridge
    assert fake_bridge.bridge.peak_depth == 1


def test_an_approved_result_reaches_the_next_worker_as_untrusted_data(admin_client, db, auto, fake_bridge):
    wf = h.planned(db, "1. Research part [research]\n2. Build part [engineering] (after 1)")
    h.drive(db, wf)
    first, second = h.tasks_of(db, wf).values()
    assert first.lifecycle == second.lifecycle == "COMPLETED"
    prompt2 = fake_bridge.turns(session_id(2))[0]
    result1 = h.subs(db, first)[0].content
    assert "UNTRUSTED DATA" in prompt2 and "output of prerequisite task" in prompt2
    assert result1[:200] in prompt2 and prompt2.index("TASK: [MOCK] Build part") < prompt2.index(result1[:200])
    assert "REMINDER: the platform policy above still applies" in prompt2
    assert "Build part" not in fake_bridge.turns(session_id(1))[0]  # W1 never saw W2's task


def test_duplicate_and_repeated_events_change_nothing(admin_client, db, auto, fake_bridge):
    wf = h.planned(db, "1. Summarise the options [research]")
    h.tick(wf)
    assert h.tick(wf)["dispatched"] == [] and h.tick(wf)["dispatched"] == []  # in flight: no second execution
    t = next(iter(h.tasks_of(db, wf).values()))
    assert db.scalar(select(func.count()).select_from(Execution).where(Execution.task_id == t.id)) == 1
    ex = execution_of(db, t)
    asyncio.run(runtime.run_execution(ex.id))
    assert asyncio.run(runtime.run_execution(ex.id)) == "COMPLETED"  # a duplicate run command: already done
    assert asyncio.run(scheduler.on_execution_finished(db, ex.id)) == "submitted"
    assert asyncio.run(scheduler.on_execution_finished(db, ex.id)) == "ignored"   # a redelivered event
    assert len(h.subs(db, t)) == 1 and len(fake_bridge.turns(session_id(1))) == 1
    h.tick(wf)
    assert asyncio.run(scheduler.on_execution_finished(db, ex.id)) == "ignored"   # even after completion
    assert h.task(db, t.id).lifecycle == "COMPLETED" and len(h.subs(db, t)) == 1


# ---------------- a restart while the request is in flight ----------------
async def _killed_mid_request(ex_id: str, fake_bridge, sent_before: int) -> None:
    """The worker process goes away after the request left: what a restart or a deploy does."""
    run = asyncio.create_task(runtime.run_execution(ex_id))
    for _ in range(300):
        if len(fake_bridge.completions()) > sent_before:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    runtime.SHUTDOWN.set()
    try:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
    finally:
        runtime.SHUTDOWN.clear()


def _in_flight(db, admin_client, fake_bridge, **settings):
    wf = h.planned(db, "1. Summarise slowly [[fake:slow:1.2]] [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    ex = execution_of(db, t)
    before = len(fake_bridge.completions())
    asyncio.run(_killed_mid_request(ex.id, fake_bridge, before))
    ex = execution_of(db, t)
    assert ex.status == "TERMINATED"  # resumable, not failed
    cp = db.scalar(select(Checkpoint).where(Checkpoint.execution_id == ex.id).order_by(Checkpoint.seq.desc()))
    assert cp.state["pending_request"]["key"] == f"acc-{ex.id}-r0"  # the open request is on record
    assert h.task(db, t.id).lifecycle == "RUNNING" and h.subs(db, t) == []
    return wf, t, ex, before


def test_a_resumed_execution_reuses_its_key_inside_the_replay_window_and_no_turn_is_repeated(admin_client, db, auto,
                                                                                            fake_bridge):
    wf, t, ex, before = _in_flight(db, admin_client, fake_bridge)
    import time
    time.sleep(1.6)  # the bridge finishes the turn on its own: the "browser" did not stop when the client left
    assert len(fake_bridge.turns(session_id(1))) == 1
    assert asyncio.run(runtime.run_execution(ex.id)) == "COMPLETED"  # the next worker process resumes it
    sent = fake_bridge.completions()[before:]
    assert [c["idempotency"] for c in sent] == [f"acc-{ex.id}-r0"] * 2  # the same request, named twice
    assert len(fake_bridge.turns(session_id(1))) == 1                   # …and delivered once
    logs = [e.payload.get("message", "") for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == ex.id, ExecutionEvent.type == "log"))]
    assert any("resending the same request with its idempotency key" in m for m in logs)
    assert asyncio.run(scheduler.on_execution_finished(db, ex.id)) == "submitted"
    sub = h.subs(db, t)[0]
    assert sub.version == 1 and sub.execution_id == ex.id and "Summarise slowly" in sub.content
    assert db.scalar(select(func.count()).select_from(Execution).where(Execution.task_id == t.id)) == 1
    h.tick(wf)
    assert h.task(db, t.id).lifecycle == "COMPLETED" and h.task(db, t.id).attempts == 1


@pytest.mark.parametrize("fallback,expected", [(True, "WAITING_FOR_ACCEPTANCE"), (False, "FAILED")])
def test_after_the_replay_window_the_request_is_not_sent_again(admin_client, db, auto, fake_bridge, fallback,
                                                               expected):
    r = admin_client.patch(f"{API}/bridges/{auto['id']}", json={"settings": {"replay_window_s": 0}})
    assert r.status_code == 200
    admin_client.patch(f"{API}/workers/{h.worker(db, 1).id}", json={"fallback_to_human": fallback})
    wf, t, ex, before = _in_flight(db, admin_client, fake_bridge)
    assert asyncio.run(runtime.run_execution(ex.id)) == "FAILED"
    assert len(fake_bridge.completions()) == before + 1  # nothing left the platform a second time
    ex = execution_of(db, t)
    assert "outcome_unknown" in ex.error and ex.error_category == "provider"
    errs = [e.payload for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == ex.id, ExecutionEvent.type == "provider_error"))]
    assert errs[-1]["code"] == "outcome_unknown" and errs[-1]["retryable"] is False

    assert asyncio.run(scheduler.on_execution_finished(db, ex.id)) == ("human_fallback" if fallback else "failed")
    task = h.task(db, t.id)
    assert task.lifecycle == expected and task.result["failures"][-1]["code"] == "outcome_unknown"
    assert task.result["failures"][-1]["action"] == ("human" if fallback else "fail")
    h.tick(wf)
    assert db.scalar(select(func.count()).select_from(Execution).where(Execution.task_id == t.id)) == 1
    assert len(fake_bridge.completions()) == before + 1  # no new attempt was started behind anyone's back
    card = admin_client.get(f"{API}/tasks/{t.id}").json()
    if fallback:
        assert card["mode"] == "HUMAN_ASSISTED" and card["fallback"]["reason"] == "outcome_unknown"
        assert "may have reached the Arena session" in card["fallback"]["note"] and card["can"]["accept"]
        assert h.worker(db, 1).mode == "AUTOMATIC"  # the worker stays Automatic; only this attempt went to a person
    else:
        assert card["error"].startswith("outcome_unknown") and card["can"]["reopen"]
    assert db.scalar(select(func.count()).select_from(TaskSubmission).where(TaskSubmission.task_id == t.id)) == 0
