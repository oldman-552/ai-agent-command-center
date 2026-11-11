"""WF-05 — four workers with independent identity, role, prompt, model/session, mode, timeout/retry and health; the
account behind a worker is shown as deterministic or pooled, exactly as far as the bridge lets anyone know.

Tier: local integration (FAKE Arena Local Bridge over real HTTP). "Four accounts" below are four fake accounts of
the fake bridge: this shows that the platform reads and reports the bridge's account model correctly — not that four
real Arena accounts were used (WF-20, NOT VERIFIED).
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Agent, AuditLog, Workforce, WorkforceWorker
from app.workforce import service
from tests import wf as h
from tests.fakebridge import ACCOUNTS, session_id

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
    rows = db.scalars(select(WorkforceWorker).where(WorkforceWorker.workforce_id == force["id"])).all()
    assert len({r.client_session_id for r in rows}) == 4  # one conversation id per worker, never shared
    assert force["accounts"] == {"automatic_workers": 0, "deterministic": 0, "pooled": 0, "unknown": 0,
                                 "distinct_accounts": 0, "shared_accounts": [], "attested": False}


def test_setup_is_idempotent_and_another_organization_gets_its_own_workforce(db):
    before = (db.scalar(select(Workforce.id).where(Workforce.organization_id == 1)),
              len(service.managed_agent_ids(db, 1)))
    assert service.ensure_default(db, 1).id == before[0]
    db.commit()
    assert len(service.managed_agent_ids(db, 1)) == before[1] == 5  # Manager + four workers, not ten
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


def test_automatic_needs_a_provider_and_a_session_and_every_mode_change_is_audited(admin_client, db, force,
                                                                                    fake_bridge):
    w1 = h.worker(db, 1)
    r = admin_client.patch(f"{API}/workers/{w1.id}", json={"mode": "AUTOMATIC"})
    assert r.status_code == 422 and r.json()["detail"] == "automatic_needs_provider_and_model"
    db.expire_all()
    assert h.worker(db, 1).mode == "HUMAN_ASSISTED"  # the refused change left nothing behind
    b = h.bridge(admin_client, fake_bridge)
    r = admin_client.patch(f"{API}/workers/{w1.id}", json={"mode": "AUTOMATIC", "provider_id": b["id"],
                                                           "model": session_id(1)})
    assert r.status_code == 200 and r.json()["mode"] == "AUTOMATIC" and r.json()["provider"]["is_bridge"] is True
    assert admin_client.patch(f"{API}/workers/{w1.id}", json={"mode": "DISABLED"}).json()["status"]["state"] == (
        "disabled")
    changes = [(a.details["from"], a.details["to"]) for a in db.scalars(
        select(AuditLog).where(AuditLog.action == "workforce.worker_mode_changed", AuditLog.agent_id == w1.agent_id)
        .order_by(AuditLog.id.desc()).limit(2))]
    assert changes == [("AUTOMATIC", "DISABLED"), ("HUMAN_ASSISTED", "AUTOMATIC")]
    assert all(a.user_id for a in db.scalars(select(AuditLog).where(
        AuditLog.action == "workforce.worker_mode_changed", AuditLog.agent_id == w1.agent_id)))


# ---------------- which account stands behind a worker ----------------
def test_four_sessions_of_four_accounts_are_shown_as_deterministic_but_never_as_attested(admin_client, db, force,
                                                                                         fake_bridge):
    b = h.all_automatic(db, admin_client, fake_bridge)
    ws = workers(admin_client)
    assert all(w["account_binding"]["type"] == "unknown" and w["account_binding"]["reason"] == "bridge_unreachable"
               for w in ws)  # nothing was read from the bridge yet: say so instead of guessing
    assert admin_client.post(f"/api/workforce/bridges/{b['id']}/health").status_code == 200
    body = admin_client.get(API).json()["workforce"]
    ws = body["workers"]
    for i, w in enumerate(ws, start=1):
        bind = w["account_binding"]
        assert bind["type"] == "deterministic_session" and bind["session"] == session_id(i)
        assert bind["attested"] is False and bind["account_usable"] is True
        assert bind["account"] == "o***@accounts.example.test"  # masked; the full address is never stored
        assert w["status"] == {"state": "ready", "reason": None} and w["shares_session_with"] == []
    assert len({w["account_binding"]["account_hash"] for w in ws}) == 4
    assert body["accounts"] == {"automatic_workers": 4, "deterministic": 4, "pooled": 0, "unknown": 0,
                                "distinct_accounts": 4, "shared_accounts": [], "attested": False}
    assert not any(a in admin_client.get(API).text for a in ACCOUNTS)
    db.expire_all()
    stored = h.worker(db, 2).account_binding  # written by the bridge health hook
    assert stored["type"] == "deterministic_session" and stored["account_hash"] == ws[1]["account_binding"][
        "account_hash"]


def test_pooled_shared_and_unknown_bindings_are_reported_as_such(admin_client, db, force, fake_bridge):
    b = h.all_automatic(db, admin_client, fake_bridge)
    service.update_worker(db, h.worker(db, 2), user=h.admin(db), patch={"model": "agent"})       # no session
    service.update_worker(db, h.worker(db, 3), user=h.admin(db), patch={"model": session_id(1)})  # W1's session
    service.update_worker(db, h.worker(db, 4), user=h.admin(db),
                          patch={"model": "11111111-2222-4333-8444-555555555555"})                 # not in the bridge
    db.commit()
    admin_client.post(f"/api/workforce/bridges/{b['id']}/health")
    body = admin_client.get(API).json()["workforce"]
    w1, w2, w3, w4 = body["workers"]
    assert w2["account_binding"]["type"] == "pooled" and w2["account_binding"]["accounts"] == 4
    assert w2["account_binding"]["reason"] == "several_accounts_no_selector"
    assert w1["shares_session_with"] == [3] and w3["shares_session_with"] == [1]  # one Arena conversation, two workers
    assert w3["account_binding"]["account_hash"] == w1["account_binding"]["account_hash"]
    assert w4["account_binding"] == {**w4["account_binding"], "type": "unknown", "reason": "session_not_found"}
    assert body["accounts"] == {**body["accounts"], "deterministic": 2, "pooled": 1, "unknown": 1,
                                "distinct_accounts": 1, "shared_accounts": [["W1", "W3"]], "attested": False}
    # the owner of W1's session is disabled in the bridge: W1 cannot work automatically and falls back to a person
    fake_bridge.bridge.accounts[0]["disabled"] = True
    admin_client.post(f"/api/workforce/bridges/{b['id']}/health")
    w1 = workers(admin_client)[0]
    assert w1["account_binding"]["account_usable"] is False
    assert w1["status"] == {"state": "fallback", "reason": "account_unavailable"}
    # the latest health reading may record several workers' changes (the pooled W2 can change too): look at all
    changes = db.scalars(select(AuditLog).where(AuditLog.action == "workforce.worker_binding_changed")
                         .order_by(AuditLog.id.desc()).limit(4)).all()
    assert any(c.details["slot"] in (1, 3) for c in changes)


def test_worker_status_says_why_an_automatic_worker_cannot_work(admin_client, db, force, fake_bridge):
    b = h.all_automatic(db, admin_client, fake_bridge)
    admin_client.post(f"/api/workforce/bridges/{b['id']}/health")
    assert [w["status"]["state"] for w in workers(admin_client)] == ["ready"] * 4
    # terms revoked: nothing may be sent, so the worker's tasks go to a person
    admin_client.delete(f"/api/workforce/bridges/{b['id']}/terms")
    assert workers(admin_client)[0]["status"] == {"state": "fallback", "reason": "terms_not_acknowledged"}
    service.update_worker(db, h.worker(db, 1), user=h.admin(db), patch={"fallback_to_human": False})
    db.commit()
    assert workers(admin_client)[0]["status"] == {"state": "unavailable", "reason": "terms_not_acknowledged"}
    admin_client.post(f"/api/workforce/bridges/{b['id']}/terms", json={"statement": b["terms"]["statement"]})
    # the bridge goes away: a fresh reading says so
    fake_bridge.stop()
    admin_client.post(f"/api/workforce/bridges/{b['id']}/health")
    ws = workers(admin_client)
    assert ws[0]["status"] == {"state": "unavailable", "reason": "bridge_unavailable"}
    assert ws[1]["status"] == {"state": "fallback", "reason": "bridge_unavailable"}
    assert ws[1]["account_binding"]["reason"] == "bridge_unreachable"


def test_a_provider_that_is_not_a_bridge_has_no_account_behind_it(admin_client, db, force):
    mock = h.mock_provider(db)
    h.automatic(db, 1, mock.id, "mock-model")
    w = workers(admin_client)[0]
    assert w["provider"]["kind"] == "mock" and w["provider"]["is_mock"] is True
    assert w["account_binding"]["type"] == "not_applicable" and w["status"]["state"] == "ready"


def test_options_list_sessions_with_their_masked_owner(admin_client, db, force, fake_bridge):
    b = h.bridge(admin_client, fake_bridge)
    admin_client.post(f"/api/workforce/bridges/{b['id']}/discover")
    opts = admin_client.get(f"{API}/options").json()
    entry = next(p for p in opts["providers"] if p["id"] == b["id"])
    assert entry["is_bridge"] and entry["terms_acknowledged"] and entry["usable"] and entry["is_mock"] is True
    assert [s["id"] for s in entry["sessions"]] == [session_id(i) for i in (1, 2, 3, 4)]
    assert entry["sessions"][0]["account"] == "o***@accounts.example.test" and entry["pooled_models"] == [
        "active", "agent"]
    assert {m["name"] for m in entry["models"]} == {session_id(i) for i in (1, 2, 3, 4)}
    assert any(p["kind"] == "mock" for p in opts["providers"])
    assert "admin" in [u["username"] for u in opts["people"]] and not any(a in str(opts) for a in ACCOUNTS)


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
