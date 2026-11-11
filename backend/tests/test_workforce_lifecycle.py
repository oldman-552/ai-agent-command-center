"""WF-07 — what a task carries, and its lifecycle: transitions are validated and recorded; the coarse status and
the detailed lifecycle cannot diverge.

Tier: mock (mock Manager; Human-Assisted workers need no provider at all).
"""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Task, TaskLifecycle, TaskTransition
from app.models.work import COARSE_STATUS
from app.workforce import lifecycle
from app.workforce.lifecycle import ALLOWED, IllegalTransition, move
from tests import wf as h

L = TaskLifecycle
CHAIN = """Deliver:
1. First [research]
2. Second [engineering] (after 1)
3. Third [qa] (after 2)"""


@pytest.fixture()
def force(db):
    return h.reset(db)


def task(db, state: str = "DRAFT") -> Task:
    t = Task(organization_id=1, title="lifecycle probe", lifecycle=state)
    db.add(t)
    db.commit()
    return t


def trail(db, t: Task) -> list[tuple[str | None, str, str]]:
    db.expire_all()
    return [(x.from_state, x.to_state, x.actor) for x in lifecycle.history(db, t.id)]


# ---------------- the table ----------------
def test_the_table_covers_every_state_and_only_known_states():
    states = {s.value for s in L}
    assert set(ALLOWED) == states and all(targets <= states for targets in ALLOWED.values())
    assert ALLOWED["COMPLETED"] == frozenset() and ALLOWED["CANCELLED"] == frozenset()
    assert ALLOWED["FAILED"] == {"READY"} and ALLOWED["REJECTED"] == {"READY"}  # only a reopening leaves them
    assert "COMPLETED" not in ALLOWED["UNDER_REVIEW"]  # never completed without passing APPROVED
    assert "COMPLETED" not in ALLOWED["RUNNING"] and "COMPLETED" not in ALLOWED["SUBMITTED"]
    assert all(s in COARSE_STATUS for s in states)


@pytest.mark.parametrize("src,dst", [p for p in itertools.product([s.value for s in L], repeat=2) if p[0] != p[1]])
def test_every_pair_of_states_is_either_allowed_and_recorded_or_refused(db, src, dst):
    t = task(db, src)
    actor = "user:1"  # a person: so that only the table decides here
    if dst in ALLOWED[src]:
        row = move(db, t, dst, actor=actor, reason="probe", details={"k": 1})
        db.commit()
        db.refresh(t)
        assert t.lifecycle == dst and t.status == COARSE_STATUS[dst]
        assert (row.from_state, row.to_state, row.actor, row.reason, row.details) == (src, dst, actor, "probe",
                                                                                     {"k": 1})
        assert trail(db, t) == [(src, dst, actor)]  # exactly one row: no second one from the safety net
    else:
        with pytest.raises(IllegalTransition, match=f"{src}->{dst}"):
            move(db, t, dst, actor=actor)
        db.rollback()
        db.refresh(t)
        assert t.lifecycle == src and trail(db, t) == []


def test_moving_to_the_same_state_is_not_a_transition(db):
    t = task(db, "READY")
    assert move(db, t, "READY", actor="manager") is None
    db.commit()
    assert trail(db, t) == []


def test_only_a_person_can_reopen_settled_work(db):
    for state in ("FAILED", "REJECTED"):
        t = task(db, state)
        for actor in ("manager", "system", "agent:5"):
            with pytest.raises(IllegalTransition, match="only a person"):
                move(db, t, "READY", actor=actor)
        move(db, t, "READY", actor="user:7", reason="second chance")
        db.commit()
        assert trail(db, t) == [(state, "READY", "user:7")]


def test_timestamps_follow_the_lifecycle(db):
    t = task(db, "READY")
    assert t.started_at is None and t.finished_at is None
    move(db, t, "ASSIGNED", actor="manager")
    assert t.started_at is not None
    for s in ("QUEUED", "RUNNING", "FAILED"):
        move(db, t, s, actor="manager")
    assert t.finished_at is not None
    move(db, t, "READY", actor="user:1")
    db.commit()
    assert t.finished_at is None and t.started_at is not None


# ---------------- status and lifecycle cannot diverge ----------------
def test_code_that_only_knows_the_status_still_leaves_a_consistent_task_and_a_history_row(db):
    t = task(db, "PLANNED")
    t.status = "IN_PROGRESS"          # what the standard orchestrator does
    db.commit()
    db.refresh(t)
    assert (t.status, t.lifecycle) == ("IN_PROGRESS", "RUNNING")
    t.status = "REVIEW"
    db.commit()
    assert trail(db, t) == [("PLANNED", "RUNNING", "system"), ("RUNNING", "UNDER_REVIEW", "system")]
    row = db.scalar(select(TaskTransition).where(TaskTransition.task_id == t.id).order_by(TaskTransition.id.desc()))
    assert row.details == {"direct": True}
    # a move through the service followed by a direct change: two rows, in order, none doubled
    move(db, t, "APPROVED", actor="user:1")
    t.status = "COMPLETED"
    db.commit()
    assert trail(db, t)[-2:] == [("UNDER_REVIEW", "APPROVED", "user:1"), ("APPROVED", "COMPLETED", "system")]


def test_the_database_itself_refuses_a_divergent_pair(db):
    t = task(db, "READY")
    for sql in ("UPDATE tasks SET status = 'COMPLETED' WHERE id = :i",
                "UPDATE tasks SET lifecycle = 'RUNNING' WHERE id = :i",
                "UPDATE tasks SET lifecycle = 'NOT_A_STATE' WHERE id = :i"):
        with pytest.raises(IntegrityError):
            db.execute(text(sql), {"i": t.id})
        db.rollback()
    db.execute(text("UPDATE tasks SET status = 'IN_PROGRESS', lifecycle = 'ACCEPTED' WHERE id = :i"), {"i": t.id})
    db.commit()


# ---------------- a planned task carries everything ----------------
def test_a_task_carries_objective_instructions_output_skills_worker_priority_dependencies_and_criteria(
        admin_client, db, force):
    wf = h.planned(db, CHAIN)
    first, second, third = h.tasks_of(db, wf).values()
    second.timeout_seconds = 300
    db.commit()
    r = admin_client.get(f"/api/workforce/tasks/{second.id}")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["title"] == "[MOCK] Second" and t["lifecycle"] == "PLANNED" and t["status"] == "PLANNED"
    assert t["task_objective"] and t["instructions"] and t["expected_output"]
    assert t["required_skills"] == ["engineering"] and t["priority"] == 5 and t["timeout_seconds"] == 300
    assert t["acceptance_criteria"] == ["[MOCK] The result addresses: Second"] and t["criteria"] == 1
    assert t["worker"]["label"] == "W2" and t["assignment_source"] == "plan" and t["mode"] is None
    assert [d["id"] for d in t["depends_on"]] == [first.id] and [d["id"] for d in t["dependents"]] == [third.id]
    assert [(x["from"], x["to"], x["actor"]) for x in t["transitions"]] == [(None, "PLANNED", "manager")]
    assert t["objective"]["id"] == wf.id and t["submissions"] == []


# ---------------- dependencies drive readiness ----------------
def test_a_task_waits_for_its_prerequisites_and_is_blocked_when_one_fails(admin_client, db, force):
    wf = h.planned(db, CHAIN)
    summary = h.tick(wf)
    first, second, third = h.tasks_of(db, wf).values()
    assert summary["offered"] == [first.id]
    assert (first.lifecycle, second.lifecycle, third.lifecycle) == ("WAITING_FOR_ACCEPTANCE", "PLANNED", "PLANNED")
    assert trail(db, first) == [(None, "PLANNED", "manager"), ("PLANNED", "READY", "manager"),
                                ("READY", "ASSIGNED", "manager"), ("ASSIGNED", "WAITING_FOR_ACCEPTANCE", "manager")]
    assert h.tick(wf)["offered"] == []  # a second round changes nothing
    assert len(trail(db, first)) == 4

    # the first task is cancelled: everything behind it is blocked, and says by what
    r = admin_client.post(f"/api/workforce/tasks/{first.id}/cancel", json={"note": "not needed"})
    assert r.status_code == 200 and r.json()["lifecycle"] == "CANCELLED"
    out = h.tick(wf)
    first, second, third = h.tasks_of(db, wf).values()
    assert (second.lifecycle, third.lifecycle) == ("BLOCKED", "PLANNED")  # third waits for second, which is blocked
    assert second.status == "BLOCKED" and "prerequisite" in second.error
    blocked = db.scalar(select(TaskTransition).where(TaskTransition.task_id == second.id,
                                                     TaskTransition.to_state == "BLOCKED"))
    assert blocked.details == {"blocked_by": [first.id]}
    assert out["finished"] is False and out["status"] == "running"  # third is still PLANNED behind a blocked task


def test_an_objective_whose_remaining_tasks_cannot_move_is_finished_and_reported(admin_client, db, force):
    wf = h.planned(db, "Deliver:\n1. First [research]\n2. Second [engineering] (after 1)")
    h.tick(wf)
    first, _second = h.tasks_of(db, wf).values()
    admin_client.post(f"/api/workforce/tasks/{first.id}/cancel", json={"note": "not needed"})
    out = h.tick(wf)
    assert out["finished"] is True and out["status"] == "failed" and out["report_version"] == 1
    assert h.states(db, wf) == {"First": "CANCELLED", "Second": "BLOCKED"}
    rep = admin_client.get(f"/api/workforce/objectives/{wf.id}/report").json()
    assert rep["status"] == "failed" and rep["content"]["counts"]["incomplete"] == 2


def test_cancelling_an_objective_cancels_open_tasks_and_keeps_the_history(admin_client, db, force):
    wf = h.planned(db, CHAIN)
    h.tick(wf)
    r = admin_client.post(f"/api/workforce/objectives/{wf.id}/cancel", json={"reason": "changed my mind"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert set(h.states(db, wf).values()) == {"CANCELLED"}
    first = next(iter(h.tasks_of(db, wf).values()))
    admin_id = h.admin(db).id
    assert trail(db, first)[-1] == ("WAITING_FOR_ACCEPTANCE", "CANCELLED", f"user:{admin_id}")
    # the generic workflow route does the same thing, with the same attribution
    wf2 = h.planned(db, CHAIN)
    h.tick(wf2)
    r = admin_client.post(f"/api/workflows/{wf2.id}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    t = next(iter(h.tasks_of(db, wf2).values()))
    assert trail(db, t)[-1] == ("WAITING_FOR_ACCEPTANCE", "CANCELLED", f"user:{admin_id}")


def test_the_standard_review_route_does_not_touch_workforce_tasks(admin_client, db, force):
    wf = h.planned(db, CHAIN)
    first = next(iter(h.tasks_of(db, wf).values()))
    r = admin_client.post(f"/api/tasks/{first.id}/review", json={"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == "managed_at:/api/workforce/tasks"
    from app.orchestration import scheduler as standard
    assert standard.advance(db, wf.id) == {"skipped": "workforce"}  # and the standard scheduler leaves it alone
    assert h.states(db, wf)["First"] == "PLANNED"
