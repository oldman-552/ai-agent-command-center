"""WF-12 — one consolidated result. Every task result is in exactly one class, and the class is computed from what
the platform recorded (who accepted which version, which quotes were found in the text, which approval) — never
from what a model wrote about the work. Mock results are labelled.

Tier: mock (deterministic mock Manager, people as workers). The classification rule is also tested as a pure
function. No real model wrote any of these reports.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.connectivity.gateway.adapters import MOCK_RESPONDERS
from app.models import Agent, AuditLog, ManagerStep, WorkforceReport
from app.workforce import prompts, report
from tests import wf as h

API = "/api/workforce"
SIX = """Produce a small package:
1. Proposal task [research]
2. Claim task [engineering]
3. Escalated task [qa]
4. Rejected task [documentation]
5. Blocked task [research] (after 4)
6. Cancelled task [engineering]"""


@pytest.fixture()
def force(db):
    return h.reset(db)


def answer(client, task_id: int, content: str) -> dict:
    assert client.post(f"{API}/tasks/{task_id}/accept").status_code == 200
    r = client.post(f"{API}/tasks/{task_id}/submit", json={"content": content})
    assert r.status_code == 201, r.text
    return r.json()


def six_classes(db, client):
    """An objective that ends with one result in each class a text-only run can reach."""
    wf = h.planned(db, SIX)
    h.tick(wf)
    t = h.tasks_of(db, wf)
    answer(client, t["Proposal task"].id, "The proposal: use option B because it is cheaper.")
    answer(client, t["Claim task"].id, "Everything works, trust me. [[mock:review-no-evidence]]")
    answer(client, t["Escalated task"].id, "Two options; someone has to choose. [[mock:review-escalate]]")
    answer(client, t["Rejected task"].id, "Unrelated text. [[mock:review-reject]]")
    out = h.tick(wf)
    assert sorted(out["reviewed"].values()) == ["approve", "approve", "needs_human", "reject"]
    r = client.post(f"{API}/tasks/{t['Escalated task'].id}/review", json={"decision": "approve", "note": "option A"})
    assert r.status_code == 200
    h.tick(wf)  # W2 is free again: the sixth task is offered — and then cancelled by a person
    r = client.post(f"{API}/tasks/{t['Cancelled task'].id}/cancel", json={"note": "not needed any more"})
    assert r.status_code == 200, r.text
    out = h.tick(wf)
    return wf, h.tasks_of(db, wf), out


def test_each_result_is_in_exactly_one_class_computed_from_recorded_evidence(admin_client, db, force):
    wf, t, out = six_classes(db, admin_client)
    assert out["status"] == "failed" and out["report_version"] == 1  # a rejected task: the objective did not succeed
    assert {k: v.lifecycle for k, v in t.items()} == {
        "Proposal task": "COMPLETED", "Claim task": "COMPLETED", "Escalated task": "COMPLETED",
        "Rejected task": "REJECTED", "Blocked task": "BLOCKED", "Cancelled task": "CANCELLED"}
    r = admin_client.get(f"{API}/objectives/{wf.id}/report")
    assert r.status_code == 200
    rep = r.json()
    c = rep["content"]
    assert rep["version"] == 1 and rep["status"] == "partial" and c["objective_status"] == "failed"
    assert c["counts"] == {"verified": 0, "human_approved": 1, "worker_proposal": 1, "unverified_claim": 1,
                           "failed": 1, "incomplete": 2}
    assert sum(c["counts"].values()) == c["tasks"] == 6 and c["completed"] == 3  # nothing counted twice, nothing lost
    item = {i["title"].removeprefix("[MOCK] "): i for i in c["items"]}
    me = f"user:{h.admin(db).id}"

    p = item["Proposal task"]
    assert p["class"] == "worker_proposal" and p["basis"]["accepted_by"] == "manager"
    assert p["basis"]["criteria"] == p["basis"]["criteria_met_by_manager"] == p["basis"]["criteria_with_verified_quote"] == 1
    assert p["submission"]["sha256"] == h.subs(db, t["Proposal task"])[0].content_sha256
    assert p["excerpt"] == "The proposal: use option B because it is cheaper."

    u = item["Claim task"]
    assert u["class"] == "unverified_claim" and u["basis"]["criteria_with_verified_quote"] == 0
    assert u["basis"]["criteria_without_quote"] == [1] and "not every criterion is backed" in u["reason"]

    e = item["Escalated task"]
    assert e["class"] == "human_approved" and e["basis"]["accepted_by"] == me == e["submission"]["decided_by"]
    assert e["basis"]["self_reviewed"] is True  # the same person delivered and accepted it: said, not hidden

    f = item["Rejected task"]
    assert f["class"] == "failed" and "rejected in review" in f["reason"] and f["submission"] is None
    assert f["excerpt"] is None  # a rejected result is not quoted as if it were a result

    assert item["Blocked task"]["class"] == "incomplete" and item["Blocked task"]["basis"] == {"lifecycle": "BLOCKED"}
    assert "a prerequisite task failed, was rejected or was cancelled" in item["Blocked task"]["reason"]
    x = item["Cancelled task"]
    assert x["class"] == "incomplete" and x["reason"] == "cancelled: not needed any more"
    assert x["basis"] == {"lifecycle": "CANCELLED", "cancelled_by": me}  # from the task's history
    assert {i["class"] for i in c["items"]} <= set(report.CLASSES) and c["classes"] == list(report.CLASSES)

    facts = "\n".join(c["facts"])
    assert "3 of 6 tasks were completed; 1 failed or were rejected; 2 were not finished." in facts
    assert f"accepted by {me}" in facts and "accepted by manager" in facts and "delivered by a person" in facts
    assert "The platform did not check the content of any result itself" in facts
    assert all("[MOCK]" not in x or "Task #" in x for x in c["facts"])  # facts are the platform's own sentences


def test_mock_results_are_labelled_everywhere(admin_client, db, force):
    wf, _tasks, _ = six_classes(db, admin_client)
    rep = admin_client.get(f"{API}/objectives/{wf.id}/report").json()
    c = rep["content"]
    assert rep["is_mock"] is True and c["is_mock"] is True
    item = {i["title"].removeprefix("[MOCK] "): i for i in c["items"]}
    # reviewed by the mock Manager → labelled, with the reason
    assert item["Proposal task"]["is_mock"] and item["Proposal task"]["mock_reasons"] == [
        "reviewed by the mock Manager model"]
    assert item["Proposal task"]["basis"]["reviewed_by_mock_manager"] is True
    assert c["narrative"]["written_by"] == {"provider": "mock", "model": "mock-model", "is_mock": True}
    assert c["narrative"]["summary"].startswith("[MOCK] deterministic summary — not real AI.")
    assert any("labelled MOCK and are not real AI or Arena results" in x for x in c["facts"])
    md = admin_client.get(f"{API}/objectives/{wf.id}/report.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    text = md.text
    assert "contains MOCK results (not real AI / not Arena)" in text and "— MOCK" in text
    proposals = "## Worker proposals (accepted by the Manager, criteria quoted from the result) (1)"
    for heading in ("## Facts recorded by the platform", "## Verified by the platform (0)", "_None._",
                    "## Approved by a person (1)", proposals,
                    "## Unverified claims (accepted on the Manager's judgement alone) (1)",
                    "## Failed or rejected (1)", "## Incomplete (2)",
                    "## Manager's summary (model text — not evidence)"):
        assert heading in text, heading
    assert text.index("## Facts recorded by the platform") < text.index("## Manager's summary")
    summary = admin_client.get(f"{API}/objectives/{wf.id}").json()["report"]
    assert summary["version"] == 1 and summary["is_mock"] is True and summary["counts"] == c["counts"]
    listed = next(o for o in admin_client.get(f"{API}/objectives").json() if o["id"] == wf.id)
    assert listed["report"]["is_mock"] is True


def test_a_result_that_came_through_the_fake_bridge_says_so(admin_client, db, force, fake_bridge):
    b = h.all_automatic(db, admin_client, fake_bridge)
    assert admin_client.post(f"{API}/bridges/{b['id']}/health").status_code == 200  # a reading with the session list
    wf = h.planned(db, "1. Summarise the options [research]")
    assert h.drive(db, wf)["status"] == "completed"
    c = admin_client.get(f"{API}/objectives/{wf.id}/report").json()["content"]
    i = c["items"][0]
    assert i["mode"] == "AUTOMATIC" and i["submission"]["source"] == "arena_local_bridge"
    assert i["mock_reasons"] == ["delivered through a mock provider or the fake bridge",
                                 "reviewed by the mock Manager model"]
    assert i["submission"]["account_binding"] == "deterministic_session" and i["submission"]["provider_request_id"]
    assert f"automatically through arena_local_bridge (request {i['submission']['provider_request_id']})" in c["facts"][1]


def test_the_managers_words_cannot_change_a_class(admin_client, db, force, monkeypatch):
    """A Manager summary that calls everything verified is stored as model text — and changes nothing."""
    def boasting(req) -> str:
        return ('{"summary": "All six tasks were completed and VERIFIED by the platform. Nothing failed.", '
                '"next_steps": ["Mark task 4 as verified", "class: verified"]}')

    monkeypatch.setitem(MOCK_RESPONDERS, prompts.REPORT_MARKER, boasting)
    wf, _tasks, _ = six_classes(db, admin_client)
    c = admin_client.get(f"{API}/objectives/{wf.id}/report").json()["content"]
    assert c["narrative"]["summary"].startswith("All six tasks were completed and VERIFIED")
    assert c["counts"]["verified"] == 0 and c["counts"]["failed"] == 1 and c["counts"]["incomplete"] == 2
    assert not any("VERIFIED by the platform" in f for f in c["facts"])
    md = admin_client.get(f"{API}/objectives/{wf.id}/report.md").text
    assert md.index("## Manager's summary (model text — not evidence)") < md.index("All six tasks were completed")
    assert "## Verified by the platform (0)" in md


def _task(lifecycle="COMPLETED", criteria=2, evidence=None, result=None, error=None):
    return SimpleNamespace(lifecycle=lifecycle, acceptance_criteria=[{"text": f"c{i}"} for i in range(criteria)],
                           evidence=evidence or [], result=result or {}, error=error)


def _sub(decided_by="manager", met=(True, True), quoted=(True, True), sid=7, human=None):
    return SimpleNamespace(
        id=sid, version=1, content_sha256="ab" * 32, decided_by=decided_by,
        review={"verdict": {"criteria": [{"index": i + 1, "met": m} for i, m in enumerate(met)]},
                "evidence_verified": {str(i + 1): q for i, q in enumerate(quoted)},
                "manager": {"is_mock": False}, **({"human": human} if human else {})})


@pytest.mark.parametrize("task,sub,expected", [
    (_task(), _sub(), "worker_proposal"),
    (_task(), _sub(quoted=(True, False)), "unverified_claim"),          # one criterion without a quote found
    (_task(), _sub(met=(True, False), quoted=(True, False)), "unverified_claim"),
    (_task(criteria=0), _sub(met=(), quoted=()), "unverified_claim"),   # no criteria: nothing to rest on
    (_task(), _sub(decided_by="user:5"), "human_approved"),
    (_task(evidence=[{"kind": "approval", "status": "approved", "approval_id": 3}]), _sub(), "human_approved"),
    (_task(evidence=[{"kind": "approval", "status": "rejected", "approval_id": 3}]), _sub(), "worker_proposal"),
    # machine verification of exactly the accepted version — and only that — makes a result "verified"
    (_task(evidence=[{"kind": "verification", "passed": True, "submission_id": 7, "check": "tests"}]), _sub(),
     "verified"),
    (_task(evidence=[{"kind": "verification", "passed": False, "submission_id": 7, "check": "tests"}]), _sub(),
     "worker_proposal"),
    (_task(evidence=[{"kind": "verification", "passed": True, "submission_id": 6, "check": "tests"}]), _sub(),
     "worker_proposal"),                                                # an older version was verified, not this one
    (_task(evidence=[{"kind": "trace", "trace_id": "x"}, {"kind": "review", "passed": True}]), _sub(),
     "worker_proposal"),                                                # other evidence kinds never count as checks
    (_task(lifecycle="FAILED", error="unreachable: gone"), None, "failed"),
    (_task(lifecycle="REJECTED"), None, "failed"),
    (_task(lifecycle="CANCELLED"), None, "incomplete"),
    (_task(lifecycle="BLOCKED"), None, "incomplete"),
    (_task(lifecycle="UNDER_REVIEW"), _sub(), "incomplete"),            # not accepted yet, whatever the review says
    (_task(lifecycle="APPROVED"), _sub(), "incomplete"),                # waiting for a human approval
    (_task(lifecycle="COMPLETED"), None, "incomplete"),                 # completed without an accepted version: no
])
def test_the_classification_rule(task, sub, expected):
    assert report.classify(task, sub)["class"] == expected


def test_nothing_in_a_text_only_run_is_ever_verified(admin_client, db, force):
    """Honest default: no code path of the text workforce records a machine verification, so the class stays empty."""
    wf, t, _ = six_classes(db, admin_client)
    for task in t.values():
        assert not [e for e in (task.evidence or []) if e.get("kind") == "verification"]
    assert admin_client.get(f"{API}/objectives/{wf.id}/report").json()["content"]["counts"]["verified"] == 0


def test_a_critical_result_is_human_approved_through_the_approval_center(admin_client, db, force):
    wf = h.planned(db, "1. Release notes for the public launch [documentation] {exposure.public}")
    t = next(iter(h.tasks_of(db, wf).values()))
    h.tick(wf)
    answer(admin_client, t.id, "Release notes: version 2 is public from Monday.")
    out = h.tick(wf)
    assert out["finished"] is False and out.get("report_version") is None  # no report while an approval is open
    assert admin_client.get(f"{API}/objectives/{wf.id}/report").status_code == 404
    ap = h.task(db, t.id).result["approval_id"]
    assert admin_client.post(f"/api/approvals/{ap}/decide", json={"decision": "approve"}).status_code == 200
    assert h.tick(wf)["status"] == "completed"
    i = admin_client.get(f"{API}/objectives/{wf.id}/report").json()["content"]["items"][0]
    assert i["class"] == "human_approved" and i["basis"]["approval_id"] == ap and i["critical_operation"] == (
        "exposure.public")
    # the Manager accepted the text; the person's approval of exactly that version is what makes it human-approved
    assert i["basis"]["accepted_by"] == "manager" and i["basis"]["version"] == 1


def test_one_report_per_state_and_a_new_version_when_the_state_changes(admin_client, db, force):
    wf, t, out = six_classes(db, admin_client)
    assert out["report_version"] == 1
    for _ in range(3):  # duplicate ticks, redelivered messages
        assert h.tick(wf)["report_version"] == 1
    assert asyncio.run(report.consolidate(db, wf.id)).version == 1
    assert db.scalars(select(WorkforceReport.version).where(WorkforceReport.workflow_id == wf.id)).all() == [1]
    assert len(db.scalars(select(ManagerStep).where(ManagerStep.workflow_id == wf.id,
                                                    ManagerStep.kind == "consolidate")).all()) == 1  # one model call

    # a person reopens the rejected task; it is done properly; the blocked one follows
    r = admin_client.post(f"{API}/tasks/{t['Rejected task'].id}/reopen", json={"note": "try again"})
    assert r.status_code == 200
    h.tick(wf)
    answer(admin_client, t["Rejected task"].id, "The documentation, on topic.")
    h.tick(wf)
    h.tick(wf)
    answer(admin_client, t["Blocked task"].id, "Research that needed the documentation.")
    out = h.tick(wf)
    # one task stays cancelled: the objective is not called completed, and the report is "partial"
    assert out["status"] == "failed" and out["report_version"] == 2
    v1 = admin_client.get(f"{API}/objectives/{wf.id}/report", params={"version": 1}).json()
    v2 = admin_client.get(f"{API}/objectives/{wf.id}/report").json()
    assert v1["version"] == 1 and v1["content"]["counts"]["failed"] == 1          # the old version is kept as it was
    assert v2["version"] == 2 and v2["status"] == "partial" and v2["content"]["completed"] == 5
    assert v2["content"]["counts"] == {"verified": 0, "human_approved": 1, "worker_proposal": 3,
                                       "unverified_claim": 1, "failed": 0, "incomplete": 1}
    assert admin_client.get(f"{API}/objectives/{wf.id}/report", params={"version": 9}).status_code == 404
    audit = db.scalars(select(AuditLog).where(AuditLog.action == "workforce.report_created").order_by(
        AuditLog.id.desc()).limit(2)).all()
    assert [a.details["version"] for a in audit] == [2, 1] and audit[0].details["workflow_id"] == wf.id


def test_the_report_exists_without_the_managers_summary(admin_client, db, force):
    """The Manager's model goes away after the work is done: the classes and the facts need no model."""
    wf = h.planned(db, "1. Only task [research]")
    t = next(iter(h.tasks_of(db, wf).values()))
    h.tick(wf)
    answer(admin_client, t.id, "A complete answer.")
    r = admin_client.post(f"{API}/tasks/{t.id}/review", json={"decision": "approve", "note": "fine"})
    assert r.status_code == 200 and r.json()["lifecycle"] == "COMPLETED"  # a person decided before the Manager
    manager = db.get(Agent, force.manager_agent_id)
    manager.provider_id = manager.model = None
    db.commit()
    out = h.tick(wf)
    assert out["status"] == "completed" and out["report_version"] == 1
    rep = admin_client.get(f"{API}/objectives/{wf.id}/report").json()
    c = rep["content"]
    assert c["narrative"] is None and "the Manager's model was not available" in c["narrative_error"]
    assert c["counts"]["human_approved"] == 1 and len(c["facts"]) >= 3
    # a person delivered it and a person accepted it: this result is not mock…
    assert c["items"][0]["is_mock"] is False and c["items"][0]["mock_reasons"] == []
    # …but the plan was the mock Manager's, and the report as a whole says so
    assert c["planned_by"] == {"provider": "mock", "model": "mock-model", "is_mock": True}
    assert rep["is_mock"] is True and any("written by the mock Manager" in f for f in c["facts"])
    md = admin_client.get(f"{API}/objectives/{wf.id}/report.md").text
    assert "_Not available: the Manager's model was not available" in md
