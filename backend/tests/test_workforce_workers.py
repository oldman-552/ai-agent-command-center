"""WF-05 — four workers with independent identity, role, prompt, model/session, mode, timeout/retry and health.

Tier: mock (MOCK provider for Automatic workers). The account tests of Arena Local Bridge were removed with the bridge
(ADR-038); a browser-window worker's binding is tested in test_workforce_browser.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Agent, AuditLog, Workforce
from app.workforce import service
from tests import wf as h

API = "/api/workforce"


@pytest.fixture()
def force(db):
    return h.reset(db)


def workers(client) -> list[dict]:
    return client.get(API).json()["workforce"]["workers"]


def test_the_default_workforce_has_a_manager_without_a_model_and_four_independent_human_assisted_workers(
        admin_client, db):
    h.reset(db, manager=None)
    body = admin_client.get(API).json()
    force = body["workforce"]
    assert body["default_mode"] == "HUMAN_ASSISTED" and body["modes"] == ["AUTOMATIC", "HUMAN_ASSISTED", "DISABLED"]
    assert force["manager"]["configured"] is False and force["manager"]["model"] is None  # nobody chose one yet
    ws = force["workers"]
    assert [w["label"] for w in ws] == ["W1", "W2", "W3", "W4"]
    assert [w["name"] for w in ws] == ["Worker 1 — Research & Analysis", "Worker 2 — Engineering & Development",
                                       "Worker 3 — QA & Security", "Worker 4 — Documentation & Strategy"]
    assert [w["role"] for w in ws] == ["research", "engineering", "qa", "documentation"]
    assert all(w["mode"] == "HUMAN_ASSISTED" and w["provider"] is None and w["model"] is None for w in ws)
    assert all(w["status"] == {"state": "human", "reason": None} and w["fallback_to_human"] for w in ws)
    assert all(w["account_binding"] == {"type": "unconfigured", "attested": False} for w in ws)
    assert len({w["agent_id"] for w in ws}) == 4 and len({w["identifier"] for w in ws}) == 4
    assert len({w["prompt"] for w in ws}) == 4 and all("You are one worker in a team" in w["prompt"] for w in ws)
    assert all(w["timeout_seconds"] >= 30 and w["retry_policy"]["max_attempts"] >= 1 for w in ws)
    rows = service.workers(db, force["id"])  # workers archived by another test (ADR-036) are not part of the team
    assert len({r.client_session_id for r in rows}) == 4  # one conversation id per worker, never shared
    assert force["accounts"] == {"automatic_workers": 0, "deterministic": 0, "pooled": 0, "unknown": 0,
                                 "distinct_accounts": 0, "shared_accounts": [], "attested": False}


def test_setup_is_idempotent_and_another_organization_gets_its_own_workforce(db):
    def active() -> int:  # the Manager plus the workers that are not archived (ADR-036)
        return 1 + len(service.workers(db, service.get(db, 1).id))
    before = (db.scalar(select(Workforce.id).where(Workforce.organization_id == 1)), active())
    assert service.ensure_default(db, 1).id == before[0]
    db.commit()
    assert active() == before[1] == 5  # Manager + four workers, not ten
    from app.models import Organization
    org = db.scalar(select(Organization).where(Organization.slug == "wf-second-org"))
    if org is None:
        org = Organization(slug="wf-second-org", name="Second")
        db.add(org)
        db.flush()
    other = service.ensure_default(db, org.id)
    db.commit()
    assert other.id != before[0] and other.organization_id == org.id
    assert not service.managed_agent_ids(db, org.id) & service.managed_agent_ids(db, 1)


def test_each_worker_is_configured_on_its_own(admin_client, db, force):
    w2, w3 = h.worker(db, 2), h.worker(db, 3)
    r = admin_client.patch(f"{API}/workers/{w2.id}", json={
        "name": "Backend Builder", "prompt": "You write Go services.", "skills": ["Engineering", " go "],
        "timeout_seconds": 1200, "retry_policy": {"max_attempts": 4, "backoff_seconds": 10, "backoff_multiplier": 3},
        "account_label": "my second Arena account", "fallback_to_human": False, "concurrency_limit": 2})
    assert r.status_code == 200, r.text
    w = r.json()
    assert (w["name"], w["prompt"], w["skills"]) == ("Backend Builder", "You write Go services.",
                                                     ["engineering", "go"])
    assert w["timeout_seconds"] == 1200 and w["retry_policy"]["max_attempts"] == 4 and w["concurrency_limit"] == 2
    assert w["account_label"] == "my second Arena account" and w["fallback_to_human"] is False
    other = next(x for x in workers(admin_client) if x["id"] == w3.id)
    assert other["name"] == "Worker 3 — QA & Security" and other["timeout_seconds"] == 900  # untouched
    for bad in ({"timeout_seconds": 5}, {"mode": "SOMETIMES"}, {"retry_policy": {"max_attempts": 0}},
                {"unknown_field": 1}, {"concurrency_limit": 0}, {"provider_id": 999_999}):
        assert admin_client.patch(f"{API}/workers/{w2.id}", json=bad).status_code == 422, bad


def test_automatic_needs_a_provider_and_a_session_and_every_mode_change_is_audited(admin_client, db, force):
    w1 = h.worker(db, 1)
    r = admin_client.patch(f"{API}/workers/{w1.id}", json={"mode": "AUTOMATIC"})
    assert r.status_code == 422 and r.json()["detail"] == "automatic_needs_provider_and_model"
    db.expire_all()
    assert h.worker(db, 1).mode == "HUMAN_ASSISTED"  # the refused change left nothing behind
    mock = h.mock_provider(db)
    r = admin_client.patch(f"{API}/workers/{w1.id}", json={"mode": "AUTOMATIC", "provider_id": mock.id,
                                                           "model": "mock-model"})
    assert r.status_code == 200 and r.json()["mode"] == "AUTOMATIC" and r.json()["provider"]["kind"] == "mock"
    assert admin_client.patch(f"{API}/workers/{w1.id}", json={"mode": "DISABLED"}).json()["status"]["state"] == (
        "disabled")
    changes = [(a.details["from"], a.details["to"]) for a in db.scalars(
        select(AuditLog).where(AuditLog.action == "workforce.worker_mode_changed", AuditLog.agent_id == w1.agent_id)
        .order_by(AuditLog.id.desc()).limit(2))]
    assert changes == [("AUTOMATIC", "DISABLED"), ("HUMAN_ASSISTED", "AUTOMATIC")]
    assert all(a.user_id for a in db.scalars(select(AuditLog).where(
        AuditLog.action == "workforce.worker_mode_changed", AuditLog.agent_id == w1.agent_id)))


def test_worker_status_says_why_an_automatic_worker_cannot_work(admin_client, db, force):
    h.all_automatic(db)
    assert [w["status"]["state"] for w in workers(admin_client)] == ["ready"] * 4
    # the provider is switched off: nothing may be sent, so the worker's tasks go to a person
    mock = h.mock_provider(db)
    mock.enabled = False
    db.commit()
    try:
        assert workers(admin_client)[0]["status"] == {"state": "fallback", "reason": "provider_disabled"}
        service.update_worker(db, h.worker(db, 1), user=h.admin(db), patch={"fallback_to_human": False})
        db.commit()
        assert workers(admin_client)[0]["status"] == {"state": "unavailable", "reason": "provider_disabled"}
    finally:
        db.rollback()
        h.mock_provider(db).enabled = True
        db.commit()
    assert workers(admin_client)[0]["status"] == {"state": "ready", "reason": None}


def test_a_provider_that_is_not_a_bridge_has_no_account_behind_it(admin_client, db, force):
    mock = h.mock_provider(db)
    h.automatic(db, 1, mock.id, "mock-model")
    w = workers(admin_client)[0]
    assert w["provider"]["kind"] == "mock" and w["provider"]["is_mock"] is True
    assert w["account_binding"]["type"] == "not_applicable" and w["status"]["state"] == "ready"


def test_options_list_providers_and_people(admin_client, db, force):
    opts = admin_client.get(f"{API}/options").json()
    mock = next(p for p in opts["providers"] if p["kind"] == "mock")
    assert mock["is_mock"] is True and mock["enabled"] is True
    assert "admin" in [u["username"] for u in opts["people"]]


# ---------------- workforce agents belong to the workforce ----------------
def test_the_generic_agent_routes_do_not_change_workforce_agents(admin_client, db, force):
    agent_id = h.worker(db, 1).agent_id
    manager_id = force.manager_agent_id
    assert admin_client.get(f"/api/agents/{agent_id}").status_code == 200  # visible like any agent
    for call in (lambda: admin_client.patch(f"/api/agents/{agent_id}", json={"tool_permissions": ["repo.read"]}),
                 lambda: admin_client.post(f"/api/agents/{agent_id}/disable"),
                 lambda: admin_client.post(f"/api/agents/{agent_id}/archive"),
                 lambda: admin_client.delete(f"/api/agents/{agent_id}")):
        r = call()
        assert r.status_code == 409 and r.json()["detail"] == "managed_at:/api/workforce/workers"
    r = admin_client.patch(f"/api/agents/{manager_id}", json={"model": "something-else"})
    assert r.status_code == 409 and r.json()["detail"] == "managed_at:/api/workforce/manager"
    db.expire_all()
    assert db.get(Agent, agent_id).tool_permissions == [] and db.get(Agent, agent_id).is_enabled


def test_the_standard_orchestrator_never_picks_a_workforce_agent(db, force):
    from app.models import Project, Task
    from app.orchestration.planner import _team
    from app.orchestration.selection import select_agent
    mock = h.mock_provider(db)
    for slot in (1, 2, 3, 4):
        h.automatic(db, slot, mock.id, "mock-model")  # enabled, with a model: exactly what a selection looks for
    p = db.scalar(select(Project).where(Project.organization_id == 1, Project.name == service.DEFAULT_PROJECT))
    t = Task(organization_id=1, project_id=p.id, title="standard task", required_skills=["research"],
             status="READY")
    db.add(t)
    db.commit()
    managed = service.managed_agent_ids(db, 1)
    sel = select_agent(db, t)
    assert sel.agent is None or sel.agent.id not in managed
    assert not {c["agent_id"] for c in sel.candidates} & managed
    from app.models import Workflow
    wf = Workflow(organization_id=1, project_id=p.id, title="standard", objective="x", status="planning")
    assert not {a.id for a in _team(db, wf)} & managed
