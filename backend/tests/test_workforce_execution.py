"""WF-08 — Automatic execution: each worker gets its own request, a stable idempotency key, and no duplicate turn
after a restart (`outcome_unknown` instead of a blind resend once the replay window is over).

Tier: mock — the MOCK provider (requests recorded with `h.worker_provider`), real PostgreSQL. The restart tests give
the mock a replay window, as an at-least-once provider has (ADR-032); no real provider was used.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.connectivity.gateway import adapters
from app.models import Checkpoint, Execution, ExecutionEvent, Task, TaskSubmission, UsageRecord
from app.runtime import service as runtime
from app.workforce import scheduler
from tests import wf as h

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
def auto(db, force):
    h.all_automatic(db)


@pytest.fixture()
def sent(monkeypatch):
    """Every request the workers make, in order."""
    return h.worker_provider(monkeypatch)


def text_of(req) -> str:
    return "\n".join([req.system or ""] + [str(m.get("content") or "") for m in req.messages])


def execution_of(db, task: Task) -> Execution:
    db.expire_all()
    return db.scalar(select(Execution).where(Execution.task_id == task.id).order_by(Execution.created_at.desc()))


def test_an_automatic_task_reaches_the_workers_provider_and_comes_back_as_a_submission(admin_client, db, auto,
                                                                                       sent):
    wf = h.planned(db, "1. Summarise the options [research]")
    out = h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    ex = execution_of(db, t)
    assert out["dispatched"] == [t.id] and t.lifecycle == "QUEUED" and t.execution_mode == "AUTOMATIC"
    assert ex.status == "CREATED" and ex.idempotency_key == f"wf-task:{t.id}:attempt:1" and ex.engine == "api"
    assert ex.input_context["workforce"] == {"workflow_id": wf.id, "worker_id": h.worker(db, 1).id, "slot": 1,
                                             "attempt": 1}
    assert sent == []  # not sent yet

    assert h.run_executions(db, wf) == ["submitted"]
    assert len(sent) == 1 and sent[0].model == "mock-model"
    prompt = text_of(sent[0])
    assert h.WORKER_RULES in sent[0].system and "TASK: [MOCK] Summarise the options" in prompt
    assert "EXPECTED OUTPUT:" in prompt
    assert "ACCEPTANCE CRITERIA:\n- [MOCK] The result addresses: Summarise the options" in prompt

    ex = execution_of(db, t)
    sub = h.subs(db, t)[0]
    assert ex.status == "COMPLETED" and ex.provider == "mock" and ex.is_mock is True
    assert ex.input_tokens is not None and ex.output_tokens is not None  # the mock reports usage
    usage = db.scalar(select(UsageRecord).where(UsageRecord.execution_id == ex.id))
    assert usage.usage_known is True
    assert (sub.version, sub.mode, sub.source, sub.execution_id) == (1, "AUTOMATIC", "mock", ex.id)
    assert sub.submitted_by == f"agent:{h.worker(db, 1).agent_id}" and sub.is_mock is True
    assert sub.content == ex.output["text"] and sub.content.startswith("[MOCK — not real AI]")
    p = sub.provider
    assert p["kind"] == "mock" and p["model"] == "mock-model" and p["usage_reported"] is True

    assert list(h.tick(wf)["reviewed"].values()) == ["approve"]
    trail = [(x["from"], x["to"]) for x in admin_client.get(f"{API}/tasks/{t.id}").json()["transitions"]]
    assert trail == [(None, "PLANNED"), ("PLANNED", "READY"), ("READY", "ASSIGNED"), ("ASSIGNED", "QUEUED"),
                     ("QUEUED", "RUNNING"), ("RUNNING", "SUBMITTED"), ("SUBMITTED", "UNDER_REVIEW"),
                     ("UNDER_REVIEW", "APPROVED"), ("APPROVED", "COMPLETED")]


def test_four_workers_each_get_their_own_task_and_never_each_others(admin_client, db, auto, sent):
    wf = h.planned(db, FOUR)
    out = h.drive(db, wf)
    assert out["status"] == "completed" and set(h.states(db, wf).values()) == {"COMPLETED"}
    assert len(sent) == 4
    by_task = {t.title.removeprefix("[MOCK] "): execution_of(db, t).agent_id for t in h.tasks_of(db, wf).values()}
    assert by_task == {f"{part} part": h.worker(db, i).agent_id
                       for i, part in enumerate(("Research", "Build", "Test", "Write"), start=1)}
    for req in sent:  # one request, its own task, nobody else's
        assert sum(f"TASK: [MOCK] {other} part" in text_of(req) for other in ("Research", "Build", "Test", "Write")) == 1


def test_an_approved_result_reaches_the_next_worker_as_untrusted_data(admin_client, db, auto, sent):
    wf = h.planned(db, "1. Research part [research]\n2. Build part [engineering] (after 1)")
    h.drive(db, wf)
    first, second = h.tasks_of(db, wf).values()
    assert first.lifecycle == second.lifecycle == "COMPLETED"
    prompt1, prompt2 = text_of(sent[0]), text_of(sent[1])
    result1 = h.subs(db, first)[0].content
    assert "UNTRUSTED DATA" in prompt2 and "output of prerequisite task" in prompt2
    assert result1[:200] in prompt2 and prompt2.index("TASK: [MOCK] Build part") < prompt2.index(result1[:200])
    assert "REMINDER: the platform policy above still applies" in prompt2
    assert "Build part" not in prompt1  # W1 never saw W2's task


def test_duplicate_and_repeated_events_change_nothing(admin_client, db, auto, sent):
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
    assert len(h.subs(db, t)) == 1 and len(sent) == 1
    h.tick(wf)
    assert asyncio.run(scheduler.on_execution_finished(db, ex.id)) == "ignored"   # even after completion
    assert h.task(db, t.id).lifecycle == "COMPLETED" and len(h.subs(db, t)) == 1


# ---------------- a restart while the request is in flight ----------------
@pytest.fixture()
def slow_with_replay(monkeypatch):
    """An at-least-once provider: it names a replay window, and its answer takes a while."""
    def use(window: float):
        monkeypatch.setattr(adapters.MockAdapter, "replay_window", window, raising=False)
        return h.worker_provider(monkeypatch, slow=1.2)
    return use


async def _killed_mid_request(ex_id: str, sent: list) -> None:
    """The worker process goes away after the request left: what a restart or a deploy does."""
    run = asyncio.create_task(runtime.run_execution(ex_id))
    for _ in range(300):
        if sent:
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


def _in_flight(db, sent):
    wf = h.planned(db, "1. Summarise slowly [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    ex = execution_of(db, t)
    asyncio.run(_killed_mid_request(ex.id, sent))
    ex = execution_of(db, t)
    assert ex.status == "TERMINATED"  # resumable, not failed
    cp = db.scalar(select(Checkpoint).where(Checkpoint.execution_id == ex.id).order_by(Checkpoint.seq.desc()))
    assert cp.state["pending_request"]["key"] == f"acc-{ex.id}-r0"  # the open request is on record
    assert h.task(db, t.id).lifecycle == "RUNNING" and h.subs(db, t) == []
    return wf, t, ex


def test_a_resumed_execution_reuses_its_key_inside_the_replay_window(admin_client, db, auto, slow_with_replay):
    sent = slow_with_replay(60)
    wf, t, ex = _in_flight(db, sent)
    assert asyncio.run(runtime.run_execution(ex.id)) == "COMPLETED"  # the next worker process resumes it
    assert [r.metadata["idempotency_key"] for r in sent] == [f"acc-{ex.id}-r0"] * 2  # the same request, named twice
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
def test_after_the_replay_window_the_request_is_not_sent_again(admin_client, db, auto, slow_with_replay, fallback,
                                                               expected):
    sent = slow_with_replay(0)
    admin_client.patch(f"{API}/workers/{h.worker(db, 1).id}", json={"fallback_to_human": fallback})
    wf, t, ex = _in_flight(db, sent)
    assert asyncio.run(runtime.run_execution(ex.id)) == "FAILED"
    assert len(sent) == 1  # nothing left the platform a second time
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
    assert len(sent) == 1  # no new attempt was started behind anyone's back
    card = admin_client.get(f"{API}/tasks/{t.id}").json()
    if fallback:
        assert card["mode"] == "HUMAN_ASSISTED" and card["fallback"]["reason"] == "outcome_unknown"
        assert "may have reached the worker's provider" in card["fallback"]["note"] and card["can"]["accept"]
        assert h.worker(db, 1).mode == "AUTOMATIC"  # the worker stays Automatic; only this attempt went to a person
    else:
        assert card["error"].startswith("outcome_unknown") and card["can"]["reopen"]
    assert db.scalar(select(func.count()).select_from(TaskSubmission).where(TaskSubmission.task_id == t.id)) == 0
