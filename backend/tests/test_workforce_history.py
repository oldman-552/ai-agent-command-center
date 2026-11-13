"""WF-18 — observability: one prompt can be followed through the platform by ids — objective, thread, Manager
steps, tasks, workers, attempts, provider requests and traces — with times, retries and errors. Usage is shown only
where a provider reported it.

Tier: mock (mock Manager, MOCK provider for the Automatic worker; a transient failure is injected once).
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.models import Execution, ManagerStep, UsageRecord
from tests import wf as h

API = "/api/workforce"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@pytest.fixture()
def force(db):
    return h.reset(db)


@pytest.fixture()
def run(db, admin_client, force, monkeypatch):
    """W1 Automatic on the mock provider (its first request fails once, transiently), W2 a person."""
    h.automatic(db, 1)
    h.agent_of(db, 1).retry_policy = {"max_attempts": 2, "backoff_seconds": 0, "backoff_multiplier": 1}
    db.commit()
    h.worker_provider(monkeypatch, fail=1)
    wf = h.planned(db, "1. Research part [research]\n2. Build part [engineering] (after 1)")
    h.drive(db, wf)
    second = h.tasks_of(db, wf)["Build part"]
    admin_client.post(f"{API}/tasks/{second.id}/accept")
    admin_client.post(f"{API}/tasks/{second.id}/submit", json={"content": "built by hand"})
    assert h.drive(db, wf)["status"] == "completed"
    return wf


def test_the_history_carries_every_id_from_the_prompt_to_the_provider_request(admin_client, db, run):
    wf = run
    r = admin_client.get(f"{API}/objectives/{wf.id}/history")
    assert r.status_code == 200
    hist = r.json()
    assert hist["objective_id"] == wf.id and hist["thread_id"].startswith("workforce:") and hist["status"] == "completed"

    # Manager steps: what ran, on which model, how long, how it ended
    steps = hist["manager_steps"]
    assert [s["kind"] for s in steps] == ["analyze", "plan", "review", "review", "consolidate"]
    for s in steps:
        assert UUID.match(s["id"]) and s["status"] == "ok" and s["provider"] == "mock" and s["model"] == "mock-model"
        assert s["is_mock"] is True and s["started_at"] and s["ended_at"] and s["duration_ms"] >= 0
        assert s["agent_id"] == h.force_of(db).manager_agent_id and s["error"] is None
        assert s["trace_id"] is None or re.fullmatch(r"[0-9a-f]{32}", s["trace_id"])
        assert s["usage_reported"] is True and s["usage"]["input_tokens"] > 0  # the mock provider reports usage
    rows = {s.id: s for s in db.scalars(select(ManagerStep).where(ManagerStep.workflow_id == wf.id))}
    assert set(rows) == {s["id"] for s in steps}

    first, second = hist["tasks"]
    # an Automatic task: worker, attempt, execution, provider request, retry, error
    assert first["worker"]["label"] == "W1" and first["worker"]["id"] == h.worker(db, 1).id
    assert first["worker"]["agent_id"] == h.worker(db, 1).agent_id and first["mode"] == "AUTOMATIC"
    assert first["attempts"] == 1 and len(first["executions"]) == 1 and first["started_at"] and first["finished_at"]
    e = first["executions"][0]
    ex = db.get(Execution, e["id"])
    assert UUID.match(e["id"]) and e["idempotency_key"] == f"wf-task:{first['id']}:attempt:1"
    assert (e["status"], e["engine"], e["provider"], e["model"]) == ("COMPLETED", "api", "mock", "mock-model")
    assert e["is_mock"] is True and e["attempts"] == 2  # two tries inside one task attempt
    assert e["retries"] == [{"seq": e["retries"][0]["seq"], "at": e["retries"][0]["at"], "attempt": 2,
                             "after_seconds": 0.0}]
    assert e["provider_request_id"] == ex.provider_request_id
    assert e["created_at"] and e["started_at"] and e["ended_at"]
    # it succeeded: the earlier error is what it recovered from, not its outcome
    assert e["error"] is None and e["error_category"] is None and "rate limited" in e["recovered_from"]
    assert [t["to"] for t in first["transitions"]] == ["PLANNED", "READY", "ASSIGNED", "QUEUED", "RUNNING",
                                                      "SUBMITTED", "UNDER_REVIEW", "APPROVED", "COMPLETED"]
    assert all(t["at"] and t["actor"] for t in first["transitions"])
    s1 = first["submissions"][0]
    assert s1["execution_id"] == e["id"] and s1["provider_request_id"] == e["provider_request_id"]
    assert s1["manager_step_id"] in rows and rows[s1["manager_step_id"]].kind == "review"
    assert rows[s1["manager_step_id"]].submission_id == s1["id"] and rows[s1["manager_step_id"]].task_id == first["id"]
    assert (s1["decision"], s1["decided_by"], s1["outcome"]) == ("approved", "manager", "approve")

    # a Human-Assisted task: no execution, the person and the versions instead
    assert second["worker"]["label"] == "W2" and second["mode"] == "HUMAN_ASSISTED" and second["executions"] == []
    s2 = second["submissions"][0]
    assert s2["submitted_by"] == f"user:{h.admin(db).id}" and s2["execution_id"] is None and s2["source"] == "human"
    assert s2["provider_request_id"] is None and len(s2["sha256"]) == 64
    assert [t["to"] for t in second["transitions"]][-5:] == ["ACCEPTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED",
                                                             "COMPLETED"]
    rep = hist["reports"]
    assert [x["version"] for x in rep] == [1] and rep[0]["manager_step_id"] in rows
    assert rows[rep[0]["manager_step_id"]].kind == "consolidate"


def test_usage_is_shown_where_the_provider_reported_it(admin_client, db, run):
    wf = run
    hist = admin_client.get(f"{API}/objectives/{wf.id}/history").json()
    e = hist["tasks"][0]["executions"][0]
    assert e["usage_reported"] is True and e["usage"]["output_tokens"] > 0  # the mock provider reports usage
    ex = db.get(Execution, e["id"])
    assert ex.output_tokens == e["usage"]["output_tokens"]
    assert db.scalars(select(UsageRecord).where(UsageRecord.execution_id == ex.id)).all()
    assert "usage is listed only where a provider reported it" in hist["usage_note"]
    # a person's result has no execution and so no usage at all, not zeros
    assert hist["tasks"][1]["executions"] == []


def test_a_failed_attempt_stays_in_the_history_with_its_error(admin_client, db, force, monkeypatch):
    h.automatic(db, 1)
    h.worker_provider(monkeypatch, fail=99)  # the worker's provider stays unavailable
    wf = h.planned(db, "1. Answer strangely [research]")
    t = next(iter(h.tasks_of(db, wf).values()))
    t.max_attempts = 2
    db.commit()
    h.drive(db, wf)
    task = admin_client.get(f"{API}/objectives/{wf.id}/history").json()["tasks"][0]
    # two automatic attempts failed; the third attempt is the person's
    assert task["lifecycle"] == "WAITING_FOR_ACCEPTANCE" and task["attempts"] == 3 and task["mode"] == "HUMAN_ASSISTED"
    assert [e["status"] for e in task["executions"]] == ["FAILED", "FAILED"]
    assert [e["idempotency_key"] for e in task["executions"]] == [f"wf-task:{task['id']}:attempt:{i}" for i in (1, 2)]
    for e in task["executions"]:
        assert "rate limited (injected by the test)" in e["error"] and e["error_category"] == "transient"
        assert e["ended_at"] >= e["started_at"]
    assert [(f["attempt"], f["action"]) for f in task["failures"]] == [(1, "retry"), (2, "human")]
    assert task["failures"][0]["execution_id"] == task["executions"][0]["id"]
    moves = [(x["from"], x["to"]) for x in task["transitions"]]
    assert moves.count(("RUNNING", "READY")) == 1 and moves[-1] == ("RUNNING", "WAITING_FOR_ACCEPTANCE")


def test_the_history_shows_no_secret(admin_client, db, run):
    text = admin_client.get(f"{API}/objectives/{run.id}/history").text
    assert "api_key" not in text and "authorization" not in text.lower()


def test_history_belongs_to_its_organization(admin_client, db, run, make_user):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import login_as
    make_user("wf_hist_other", "ORGANIZATION_ADMIN", org_slug="other-org-history")
    other = login_as(TestClient(app), "wf_hist_other", "user-password-123")
    assert other.get(f"{API}/objectives/{run.id}/history").status_code == 404  # another tenant: it does not exist
    assert other.get(f"{API}/objectives/999999/history").status_code == 404
    assert admin_client.get(f"{API}/objectives/{run.id}/history").status_code == 200
