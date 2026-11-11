"""WF-09 — Human-Assisted execution: task card, accept / decline, submission of text, notes and files,
resubmission. Works end to end with no bridge at all.

Tier: mock (mock Manager). No provider is configured for any worker in this file: nothing can be sent anywhere.
"""
from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import AuditLog, Execution, Provider, Task, TaskAttachment, TaskSubmission
from app.workforce import human
from tests import wf as h
from tests.conftest import login_as

PW = "user-password-123"
API = "/api/workforce"
TWO = """Deliver:
1. Compare the options [research]
2. Write the guide [documentation] (after 1)"""


@pytest.fixture()
def force(db):
    return h.reset(db)


def fresh() -> TestClient:
    from app.main import app
    return TestClient(app)


def offered(client) -> list[dict]:
    return client.get(f"{API}/tasks", params={"queue": "offered"}).json()["tasks"]


def do(client, task_id: int, content: str, **extra) -> dict:
    assert client.post(f"{API}/tasks/{task_id}/accept").status_code == 200
    r = client.post(f"{API}/tasks/{task_id}/submit", json={"content": content, **extra})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- end to end, with no bridge ----------------
def test_an_objective_completes_end_to_end_with_no_bridge_and_no_provider(admin_client, db, force):
    assert db.scalar(select(func.count()).select_from(Provider).where(
        Provider.organization_id == 1, Provider.kind == "arena_local_bridge", Provider.enabled.is_(True),
        Provider.id.in_([a.provider_id for a in (h.agent_of(db, s) for s in (1, 2, 3, 4)) if a.provider_id]))) == 0
    wf = h.planned(db, TWO)
    h.tick(wf)
    first, second = h.tasks_of(db, wf).values()

    # the card: who it is for, what to do, and one message ready to paste into Arena's own page
    cards = offered(admin_client)
    assert [c["id"] for c in cards] == [first.id]
    card = admin_client.get(f"{API}/tasks/{first.id}").json()
    assert card["lifecycle"] == "WAITING_FOR_ACCEPTANCE" and card["mode"] == "HUMAN_ASSISTED"
    assert card["worker"]["label"] == "W1" and card["can"]["accept"] and not card["can"]["submit"]
    brief = card["brief"]["message"]
    assert "### RULES FROM THE PLATFORM" in brief and "You are one worker in a team" in brief
    assert "Research & Analysis" in brief and "### YOUR TASK" in brief and "[MOCK] Compare the options" in brief
    assert "ACCEPTANCE CRITERIA" in brief and card["brief"]["sha256"] == hashlib.sha256(brief.encode()).hexdigest()

    answer = "Option A is cheaper; option B scales better. Recommendation: B.\nAssumption: 10k users."
    sub = do(admin_client, first.id, answer, notes="pasted from my Arena session")
    assert sub["version"] == 1 and sub["mode"] == "HUMAN_ASSISTED" and sub["source"] == "human"
    assert sub["content"] == answer and sub["sha256"] == hashlib.sha256(answer.encode()).hexdigest()
    assert sub["submitted_by"] == f"user:{h.admin(db).id}" and sub["is_mock"] is False
    assert sub["provider"]["brief_sha256"] == card["brief"]["sha256"]  # what was asked is tied to what was answered
    assert h.states(db, wf) == {"Compare the options": "SUBMITTED", "Write the guide": "PLANNED"}

    out = h.tick(wf)  # the Manager reviews; the next task becomes ready and is offered
    assert list(out["reviewed"].values()) == ["approve"]
    first, second = h.tasks_of(db, wf).values()
    assert first.lifecycle == "COMPLETED" and first.result["output"] == answer
    assert second.lifecycle == "WAITING_FOR_ACCEPTANCE"

    # the second worker receives the first result — as fenced, untrusted data
    brief2 = admin_client.get(f"{API}/tasks/{second.id}").json()["brief"]["message"]
    assert answer in brief2 and "UNTRUSTED DATA" in brief2 and "output of prerequisite task" in brief2
    do(admin_client, second.id, "# Guide\nUse option B. Steps: 1) install 2) configure.")
    out = h.tick(wf)
    assert out["status"] == "completed" and out["finished"] and out["report_version"] == 1
    assert set(h.states(db, wf).values()) == {"COMPLETED"}
    assert db.scalar(select(func.count()).select_from(Execution).join(Task, Task.id == Execution.task_id)
                     .where(Task.workflow_id == wf.id)) == 0  # nothing was ever sent to a provider
    obj = admin_client.get(f"{API}/objectives/{wf.id}").json()
    assert obj["status"] == "completed" and obj["progress"]["completed"] == 2 and obj["report"]["version"] == 1


def test_the_history_of_a_human_task_names_every_step_and_person(admin_client, db, force):
    wf = h.planned(db, "1. Only task [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    do(admin_client, t.id, "The answer.")
    h.tick(wf)
    me = f"user:{h.admin(db).id}"
    trail = [(x["from"], x["to"], x["actor"]) for x in admin_client.get(f"{API}/tasks/{t.id}").json()["transitions"]]
    assert trail == [(None, "PLANNED", "manager"), ("PLANNED", "READY", "manager"), ("READY", "ASSIGNED", "manager"),
                     ("ASSIGNED", "WAITING_FOR_ACCEPTANCE", "manager"), ("WAITING_FOR_ACCEPTANCE", "ACCEPTED", me),
                     ("ACCEPTED", "SUBMITTED", me), ("SUBMITTED", "UNDER_REVIEW", "manager"),
                     ("UNDER_REVIEW", "APPROVED", "manager"), ("APPROVED", "COMPLETED", "manager")]
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.task_id == t.id)))
    assert {"workforce.task_offered", "workforce.task_accepted", "workforce.submitted",
            "workforce.result_approved"} <= actions


# ---------------- resubmission ----------------
def test_a_requested_revision_is_a_new_version_and_the_first_stays_untouched(admin_client, db, force):
    wf = h.planned(db, "1. Only task [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    first = do(admin_client, t.id, "Half an answer [[mock:review-fail]]")
    assert list(h.tick(wf)["reviewed"].values()) == ["revise"]
    card = admin_client.get(f"{API}/tasks/{t.id}").json()
    # the same task comes back as a card, with the reviewer's words on it and in the brief
    assert card["lifecycle"] == "WAITING_FOR_ACCEPTANCE" and card["attempts"] == 2
    assert card["review_notes"] == ["[MOCK] address every acceptance criterion explicitly"]
    assert "REVIEWER REQUESTED CHANGES" in card["brief"]["message"]
    second = do(admin_client, t.id, "The whole answer, criterion by criterion.")
    assert second["version"] == 2 and second["attempt"] == 2
    assert list(h.tick(wf)["reviewed"].values()) == ["approve"]
    v1, v2 = h.subs(db, t)
    assert (v1.version, v1.content, v1.decision, v1.decided_by) == (1, first["content"], "revision_requested",
                                                                    "manager")
    assert v1.content_sha256 == first["sha256"] and v1.review["outcome"] == "revise"
    assert (v2.version, v2.decision) == (2, "approved")
    db.expire_all()
    assert db.get(Task, t.id).result["version"] == 2 and db.get(Task, t.id).lifecycle == "COMPLETED"


def test_only_an_accepted_task_can_be_answered_and_only_once_per_attempt(admin_client, db, force):
    wf = h.planned(db, "1. Only task [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    r = admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": "too early"})
    assert r.status_code == 409 and r.json()["detail"] == "task_not_accepted:WAITING_FOR_ACCEPTANCE"
    assert admin_client.post(f"{API}/tasks/{t.id}/accept").status_code == 200
    assert admin_client.post(f"{API}/tasks/{t.id}/accept").status_code == 409
    for bad in ({"content": ""}, {"content": "   \n"}, {"content": "x", "unknown": 1}):
        assert admin_client.post(f"{API}/tasks/{t.id}/submit", json=bad).status_code in (409, 422)
    assert admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": "the answer"}).status_code == 201
    r = admin_client.post(f"{API}/tasks/{t.id}/submit", json={"content": "a second answer"})
    assert r.status_code == 409 and r.json()["detail"] == "task_not_accepted:SUBMITTED"
    assert len(h.subs(db, t)) == 1


# ---------------- declining ----------------
def test_a_declined_task_goes_to_another_worker_with_the_skills_or_waits_and_says_why(admin_client, db, force):
    h.agent_of(db, 4).skills = ["documentation", "research"]  # W4 can also do research
    db.commit()
    wf = h.planned(db, "1. Research task [research]\n2. Testing task [qa]")
    h.tick(wf)
    research, testing = h.tasks_of(db, wf).values()
    r = admin_client.post(f"{API}/tasks/{research.id}/decline", json={"reason": "no access to that account today"})
    assert r.status_code == 200 and r.json()["lifecycle"] == "READY"
    h.tick(wf)
    research, testing = h.tasks_of(db, wf).values()
    assert research.lifecycle == "WAITING_FOR_ACCEPTANCE" and research.assignment_source == "redistributed"
    assert research.assigned_agent_id == h.worker(db, 4).agent_id and research.attempts == 2
    card = admin_client.get(f"{API}/tasks/{research.id}").json()
    assert card["worker"]["label"] == "W4" and card["declines"][0]["reason"] == "no access to that account today"
    moved = [x for x in card["transitions"] if x["to"] == "ASSIGNED"][-1]
    assert moved["details"]["because"] == "declined" and "redistributed" in moved["reason"]

    # nobody else has the qa skill: the task waits, visibly, for a person to reassign it
    assert admin_client.post(f"{API}/tasks/{testing.id}/decline", json={"reason": "busy"}).status_code == 200
    out = h.tick(wf)
    assert out["waiting"] == {testing.id: "no_eligible_worker:declined"}
    card = admin_client.get(f"{API}/tasks/{testing.id}").json()
    assert card["lifecycle"] == "READY" and card["waiting"]["reason"] == "no_eligible_worker:declined"
    assert admin_client.post(f"{API}/tasks/{testing.id}/decline", json={"reason": ""}).status_code == 422


# ---------------- who may take a card ----------------
def test_roles_and_a_named_person_decide_who_may_work_on_a_card(admin_client, db, force, make_user):
    make_user("wf-h-viewer", "VIEWER")
    dev = make_user("wf-h-dev", "DEVELOPER")
    make_user("wf-h-dev2", "DEVELOPER")
    wf = h.planned(db, "1. First [research]\n2. Second [qa]")
    h.tick(wf)
    first, second = h.tasks_of(db, wf).values()
    with fresh() as viewer:
        login_as(viewer, "wf-h-viewer", PW)
        assert viewer.get(f"{API}/tasks/{first.id}").status_code == 200  # may look
        assert viewer.post(f"{API}/tasks/{first.id}/accept").status_code == 403
        assert viewer.get(f"{API}/tasks/{first.id}").json()["can"] == {
            "accept": False, "decline": False, "submit": False, "review": False, "reassign": False, "reopen": False,
            "cancel": False}
    with fresh() as d1, fresh() as d2:
        login_as(d1, "wf-h-dev", PW)
        login_as(d2, "wf-h-dev2", PW)
        assert d1.post(f"{API}/tasks/{first.id}/accept").status_code == 200
        # accepted by one person: nobody else answers, uploads or gives it back
        r = d2.post(f"{API}/tasks/{first.id}/submit", json={"content": "not mine"})
        assert r.status_code == 403 and r.json()["detail"] == "accepted_by_another_person"
        assert d2.post(f"{API}/tasks/{first.id}/attachments?filename=a.txt", content=b"x").status_code == 409
        assert d2.post(f"{API}/tasks/{first.id}/decline", json={"reason": "x"}).status_code == 403
        assert d1.get(f"{API}/tasks", params={"queue": "mine"}).json()["total"] == 1
        assert d2.get(f"{API}/tasks", params={"queue": "mine"}).json()["total"] == 0
        # a developer works but does not review, reassign or configure
        assert d1.post(f"{API}/tasks/{first.id}/review", json={"decision": "approve"}).status_code == 403
        assert d1.post(f"{API}/tasks/{first.id}/reassign", json={"worker_id": 1, "note": "x"}).status_code == 403
        assert d1.patch(f"{API}/workers/{h.worker(db, 1).id}", json={"mode": "DISABLED"}).status_code == 403

        # W3 gets a named person: only that person sees and takes its cards (those who configure may step in)
        r = admin_client.patch(f"{API}/workers/{h.worker(db, 3).id}", json={"assignee_user_id": dev.id})
        assert r.status_code == 200 and r.json()["assignee"]["username"] == "wf-h-dev"
        assert [c["id"] for c in offered(d1)] == [second.id] and offered(d2) == []
        r = d2.post(f"{API}/tasks/{second.id}/accept")
        assert r.status_code == 403 and r.json()["detail"] == "task_reserved_for_another_person"
        assert d1.post(f"{API}/tasks/{second.id}/accept").status_code == 200
    r = admin_client.patch(f"{API}/workers/{h.worker(db, 3).id}", json={"assignee_user_id": 999_999})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_assignee_user_id"


# ---------------- attached files ----------------
def test_files_are_stored_hashed_limited_and_only_downloaded(admin_client, db, force, monkeypatch):
    wf = h.planned(db, "1. Only task [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    url = f"{API}/tasks/{t.id}/attachments"
    assert admin_client.post(f"{url}?filename=notes.txt", content=b"early").status_code == 409  # not accepted yet
    admin_client.post(f"{API}/tasks/{t.id}/accept")

    data = b"col_a,col_b\n1,2\n"
    r = admin_client.post(f"{url}?filename=../../etc/Results%20(final).csv", content=data,
                          headers={"content-type": "text/csv"})
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["filename"] == "Results (final).csv" and a["size"] == len(data)  # the path part is dropped
    assert a["sha256"] == hashlib.sha256(data).hexdigest() and a["submission_id"] is None
    row = db.get(TaskAttachment, a["id"])
    stored = human.attachment_path(row)
    assert stored.read_bytes() == data and stored.name == a["id"] and human.storage_root() in stored.parents
    assert "Results" not in str(stored)  # never the uploader's file name on disk

    for name, body, code, detail in [("run.exe", b"MZ", 422, "file_type_not_allowed"),
                                     ("page.svg", b"<svg/>", 422, "file_type_not_allowed"),
                                     ("noextension", b"x", 422, "file_type_not_allowed"),
                                     ("empty.txt", b"", 422, "empty_file")]:
        r = admin_client.post(f"{url}?filename={name}", content=body)
        assert (r.status_code, r.json()["detail"]) == (code, detail), name
    monkeypatch.setattr(human.get_settings(), "workforce_attachment_max_bytes", 16)
    r = admin_client.post(f"{url}?filename=big.txt", content=b"x" * 17)
    assert r.status_code == 413 and r.json()["detail"] == "file_too_large"
    monkeypatch.undo()

    extra = admin_client.post(f"{url}?filename=draft.md", content=b"# draft").json()
    assert admin_client.delete(f"{API}/attachments/{extra['id']}").status_code == 204  # not submitted yet: removable
    assert db.get(TaskAttachment, extra["id"]) is None

    sub = admin_client.post(f"{API}/tasks/{t.id}/submit", json={
        "content": "See the attached table.", "notes": "exported by hand", "attachment_ids": [a["id"]]}).json()
    assert sub["attachments"] == [{"id": a["id"], "filename": "Results (final).csv", "size": len(data),
                                   "sha256": a["sha256"], "content_type": "text/csv"}]
    assert sub["notes"] == "exported by hand"
    db.expire_all()
    assert db.get(TaskAttachment, a["id"]).submission_id == sub["id"]
    r = admin_client.delete(f"{API}/attachments/{a['id']}")
    assert r.status_code == 409 and r.json()["detail"] == "attachment_is_part_of_a_submission"

    r = admin_client.get(f"{API}/attachments/{a['id']}")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-type"] == "application/octet-stream"  # never rendered as a page
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "sandbox"
    # the Manager is told that a file exists, never its content
    h.tick(wf)
    assert h.subs(db, t)[0].decision == "approved"


def test_an_attachment_of_another_task_or_tenant_cannot_be_used_or_read(admin_client, db, force, make_user):
    wf = h.planned(db, "1. First [research]\n2. Second [qa]")
    h.tick(wf)
    first, second = h.tasks_of(db, wf).values()
    for t in (first, second):
        admin_client.post(f"{API}/tasks/{t.id}/accept")
    a = admin_client.post(f"{API}/tasks/{first.id}/attachments?filename=a.txt", content=b"mine").json()
    r = admin_client.post(f"{API}/tasks/{second.id}/submit", json={"content": "x", "attachment_ids": [a["id"]]})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_attachment"
    r = admin_client.post(f"{API}/tasks/{second.id}/submit", json={"content": "x", "attachment_ids": ["nope"]})
    assert r.status_code == 422
    make_user("wf-h-other", "ORGANIZATION_ADMIN", org_slug="other-org")
    with fresh() as other:
        login_as(other, "wf-h-other", PW)
        assert other.get(f"{API}/attachments/{a['id']}").status_code == 404
        assert other.delete(f"{API}/attachments/{a['id']}").status_code == 404
        assert other.get(f"{API}/tasks/{first.id}").status_code == 404
        assert other.post(f"{API}/tasks/{first.id}/submit", json={"content": "x"}).status_code == 404
    assert db.scalar(select(func.count()).select_from(TaskSubmission).where(
        TaskSubmission.task_id.in_([first.id, second.id]))) == 0
