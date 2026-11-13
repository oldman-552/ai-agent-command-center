"""WF-10 — Disabled workers receive no new tasks; the plan is redistributed; a person can reassign, and that is
audited. Also: mixed modes in one objective, and one task at a time per worker.

Tier: mock (mock Manager, MOCK provider for the Automatic workers).
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Execution, Task, TaskTransition, UsageRecord
from tests import wf as h

API = "/api/workforce"
PLAN = """Deliver:
1. Research task [research]
2. Build task [engineering]
3. Test task [qa]"""


@pytest.fixture()
def force(db):
    return h.reset(db)


def disable(client, db, slot: int) -> None:
    r = client.patch(f"{API}/workers/{h.worker(db, slot).id}", json={"mode": "DISABLED"})
    assert r.status_code == 200, r.text


def test_a_task_planned_for_a_worker_that_was_disabled_goes_to_another_with_the_skills(admin_client, db, force):
    h.agent_of(db, 4).skills = ["documentation", "qa"]
    db.commit()
    wf = h.planned(db, PLAN)
    disable(admin_client, db, 3)  # after planning, before anything was assigned
    out = h.tick(wf)
    research, build, test = h.tasks_of(db, wf).values()
    assert sorted(out["offered"]) == sorted([research.id, build.id, test.id])
    assert test.assigned_agent_id == h.worker(db, 4).agent_id and test.assignment_source == "redistributed"
    assert research.assignment_source == build.assignment_source == "plan"
    moved = db.scalar(select(TaskTransition).where(TaskTransition.task_id == test.id,
                                                   TaskTransition.to_state == "ASSIGNED"))
    assert moved.details["worker"] == "W4" and moved.details["because"] == "worker_disabled"
    assert moved.details["previous_agent_id"] == h.worker(db, 3).agent_id and moved.actor == "manager"
    assert moved.reason == "redistributed: planned worker unavailable (worker_disabled)"
    log = db.scalar(select(AuditLog).where(AuditLog.action == "workforce.task_redistributed",
                                           AuditLog.task_id == test.id))
    assert log.details == {"from_agent_id": h.worker(db, 3).agent_id, "to_worker": "W4",
                           "because": "worker_disabled"}
    assert admin_client.get(f"{API}/tasks/{test.id}").json()["worker"]["label"] == "W4"


def test_without_an_eligible_worker_the_task_waits_with_a_reason_until_one_is_enabled(admin_client, db, force):
    wf = h.planned(db, PLAN)
    disable(admin_client, db, 3)  # nobody else has the qa skill
    out = h.tick(wf)
    test = h.tasks_of(db, wf)["Test task"]
    assert out["waiting"] == {test.id: "no_eligible_worker:worker_disabled"} and out["finished"] is False
    card = admin_client.get(f"{API}/tasks/{test.id}").json()
    assert card["lifecycle"] == "READY" and card["waiting"]["reason"] == "no_eligible_worker:worker_disabled"
    obj = admin_client.get(f"{API}/objectives/{wf.id}").json()
    assert obj["status"] == "running" and obj["waiting"] == {str(test.id): "no_eligible_worker:worker_disabled"}
    assert test.attempts == 0  # waiting is not an attempt
    r = admin_client.patch(f"{API}/workers/{h.worker(db, 3).id}", json={"mode": "HUMAN_ASSISTED"})
    assert r.status_code == 200
    out = h.tick(wf)
    test = h.tasks_of(db, wf)["Test task"]
    assert out["offered"] == [test.id] and test.lifecycle == "WAITING_FOR_ACCEPTANCE"
    assert test.assigned_agent_id == h.worker(db, 3).agent_id and test.assignment_source == "plan"
    assert admin_client.get(f"{API}/tasks/{test.id}").json()["waiting"] is None


def test_a_disabled_worker_gets_nothing_new_and_nothing_is_sent_for_it(admin_client, db, force):
    h.all_automatic(db)
    wf = h.planned(db, PLAN)
    disable(admin_client, db, 2)
    disable(admin_client, db, 3)
    h.drive(db, wf)
    research, build, test = h.tasks_of(db, wf).values()
    assert research.lifecycle == "COMPLETED"
    assert (build.lifecycle, test.lifecycle) == ("READY", "READY") and build.attempts == test.attempts == 0
    assert db.scalar(select(func.count()).select_from(Execution).where(
        Execution.task_id.in_([build.id, test.id]))) == 0
    sent_for = set(db.scalars(select(Execution.agent_id).join(Task, Task.id == Execution.task_id)
                              .where(Task.workflow_id == wf.id)))
    assert sent_for == {h.worker(db, 1).agent_id}  # only W1 ever had a request made for it
    # even an execution started by hand for a worker that is not Automatic sends nothing
    before = db.scalar(select(func.count()).select_from(UsageRecord))
    r = admin_client.post("/api/executions", json={"agent_id": h.worker(db, 2).agent_id, "input": {"prompt": "hi"}})
    assert r.status_code == 201
    ex = admin_client.get(f"/api/executions/{r.json()['id']}").json()
    assert ex["status"] == "FAILED" and "worker_not_automatic" in ex["error"]
    assert db.scalar(select(func.count()).select_from(UsageRecord)) == before


def test_disabling_does_not_take_away_a_card_that_is_already_with_a_person(admin_client, db, force):
    wf = h.planned(db, "1. Research task [research]")
    h.tick(wf)
    t = h.tasks_of(db, wf)["Research task"]
    disable(admin_client, db, 1)
    h.tick(wf)
    assert h.tasks_of(db, wf)["Research task"].lifecycle == "WAITING_FOR_ACCEPTANCE"
    assert admin_client.post(f"{API}/tasks/{t.id}/accept").status_code == 200
    assert admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": "done by hand"}).status_code == 201
    h.tick(wf)
    assert h.tasks_of(db, wf)["Research task"].lifecycle == "COMPLETED"


# ---------------- manual reassignment ----------------
def test_a_person_reassigns_a_task_and_it_is_audited(admin_client, db, force):
    wf = h.planned(db, PLAN)
    h.tick(wf)
    research = h.tasks_of(db, wf)["Research task"]
    w1, w4 = h.worker(db, 1), h.worker(db, 4)
    r = admin_client.post(f"{API}/tasks/{research.id}/reassign", json={"worker_id": w4.id, "note": "W1 is away"})
    assert r.status_code == 200 and r.json()["lifecycle"] == "READY" and r.json()["assignment_source"] == "manual"
    me = h.admin(db).id
    log = db.scalar(select(AuditLog).where(AuditLog.action == "workforce.task_reassigned",
                                           AuditLog.task_id == research.id))
    assert log.user_id == me and log.level == "warning"
    assert log.details == {"from_worker": "W1", "to_worker": "W4", "from_agent_id": w1.agent_id,
                           "to_agent_id": w4.agent_id, "note": "W1 is away"}
    h.tick(wf)
    card = admin_client.get(f"{API}/tasks/{research.id}").json()
    # a person's choice is kept even though W4 lacks the planned skill
    assert card["worker"]["label"] == "W4" and card["lifecycle"] == "WAITING_FOR_ACCEPTANCE"
    assert card["reassignments"][0]["by"] == f"user:{me}" and card["reassignments"][0]["note"] == "W1 is away"
    back = [x for x in card["transitions"] if x["to"] == "READY"][-1]
    assert back["actor"] == f"user:{me}" and back["details"]["to_worker"] == "W4"

    # a task that has not been assigned yet keeps its state, and still gets a history row
    wf2 = h.planned(db, "1. First [research]\n2. Later [engineering] (after 1)")
    later = h.tasks_of(db, wf2)["Later"]
    r = admin_client.post(f"{API}/tasks/{later.id}/reassign", json={"worker_id": w4.id, "note": "W4 knows this"})
    assert r.status_code == 200 and r.json()["lifecycle"] == "PLANNED" and r.json()["worker"]["label"] == "W4"
    rows = [(x.from_state, x.to_state) for x in db.scalars(select(TaskTransition).where(
        TaskTransition.task_id == later.id).order_by(TaskTransition.id))]
    assert rows == [(None, "PLANNED"), ("PLANNED", "PLANNED")]


def test_reassignment_is_refused_when_it_would_lose_work_or_reach_nobody(admin_client, db, force):
    h.automatic(db, 1)
    wf = h.planned(db, PLAN)
    disable(admin_client, db, 3)
    h.tick(wf)  # W1's task is now on its way to its provider
    research, build, _test = h.tasks_of(db, wf).values()
    assert research.lifecycle == "QUEUED"
    w = {s: h.worker(db, s) for s in (1, 2, 3, 4)}
    r = admin_client.post(f"{API}/tasks/{research.id}/reassign", json={"worker_id": w[2].id, "note": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "task_not_reassignable:QUEUED"  # a request is in flight
    r = admin_client.post(f"{API}/tasks/{build.id}/reassign", json={"worker_id": w[3].id, "note": "x"})
    assert r.status_code == 422 and r.json()["detail"] == "worker_disabled"
    r = admin_client.post(f"{API}/tasks/{build.id}/reassign", json={"worker_id": w[2].id, "note": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "already_assigned_to_this_worker"
    assert admin_client.post(f"{API}/tasks/{build.id}/reassign", json={"worker_id": w[4].id}).status_code == 422
    assert admin_client.post(f"{API}/tasks/{build.id}/reassign",
                             json={"worker_id": 999_999, "note": "x"}).status_code == 404
    h.drive(db, wf)
    r = admin_client.post(f"{API}/tasks/{research.id}/reassign", json={"worker_id": w[2].id, "note": "x"})
    assert r.status_code == 409 and r.json()["detail"] == "task_not_reassignable:COMPLETED"


# ---------------- mixed modes, one objective ----------------
def test_automatic_human_and_disabled_workers_finish_one_objective_together(admin_client, db, force):
    h.automatic(db, 1)                                # W1 Automatic on the mock provider
    h.agent_of(db, 4).skills = ["documentation", "qa"]  # W4 can stand in for W3
    db.commit()
    wf = h.planned(db, PLAN)
    disable(admin_client, db, 3)
    h.drive(db, wf)
    research, build, test = h.tasks_of(db, wf).values()
    assert (research.lifecycle, research.execution_mode) == ("COMPLETED", "AUTOMATIC")
    assert (build.lifecycle, build.execution_mode) == ("WAITING_FOR_ACCEPTANCE", "HUMAN_ASSISTED")
    assert test.assigned_agent_id == h.worker(db, 4).agent_id and test.lifecycle == "WAITING_FOR_ACCEPTANCE"
    for t in (build, test):
        admin_client.post(f"{API}/tasks/{t.id}/accept")
        assert admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": f"result of {t.title}"}
                                 ).status_code == 201
    out = h.drive(db, wf)
    assert out["status"] == "completed" and set(h.states(db, wf).values()) == {"COMPLETED"}
    modes = {s.mode for t in (research, build, test) for s in h.subs(db, t)}
    assert modes == {"AUTOMATIC", "HUMAN_ASSISTED"}
    assert h.subs(db, research)[0].is_mock is True and h.subs(db, build)[0].is_mock is False


def test_a_worker_takes_one_task_at_a_time(admin_client, db, force):
    wf = h.planned(db, "1. First research [research]\n2. Second research [research]")
    out = h.tick(wf)
    first, second = h.tasks_of(db, wf).values()
    assert out["offered"] == [first.id] and out["waiting"] == {second.id: "worker_busy"}
    assert second.lifecycle == "READY" and second.assigned_agent_id == first.assigned_agent_id  # not moved elsewhere
    admin_client.post(f"{API}/tasks/{first.id}/accept")
    admin_client.post(f"{API}/tasks/{first.id}/submit", json={"content": "first result"})
    out = h.tick(wf)  # the first left the worker: the second is offered in the same round as the review
    assert out["offered"] == [second.id]
    db.expire_all()
    assert db.get(Task, second.id).attempts == 1
