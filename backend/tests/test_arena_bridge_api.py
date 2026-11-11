"""WF-04 — Arena Local Bridge settings API: instances, write-only key, terms gate, diagnostics, health, discovery.

Tier: local integration (FAKE bridge over real HTTP, real PostgreSQL). Not evidence of a real Arena call.
"""
import json
import socket
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.connectivity.arena_bridge import service as bridge_service
from app.models import ApiCredential, AuditLog, ModelInfo, Provider, Role
from tests.conftest import login_as
from tests.fakebridge import ACCOUNTS, KEY, session_id

BASE = "/api/workforce/bridges"
PW = "user-password-123"


def fresh() -> TestClient:
    from app.main import app
    return TestClient(app)


def _create(client, url: str, **kw) -> dict:
    r = client.post(BASE, json={"name": f"Bridge {uuid.uuid4().hex[:8]}", "base_url": url, "api_key": KEY, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _ack(client, b: dict) -> dict:
    r = client.post(f"{BASE}/{b['id']}/terms", json={"statement": b["terms"]["statement"]})
    assert r.status_code == 200, r.text
    return r.json()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _steps(report: dict) -> dict:
    return {s["step"]: s for s in report["steps"]}


# ---------------- instances and the key ----------------
def test_key_is_write_only_encrypted_and_never_logged(admin_client, fake_bridge, db):
    b = _create(admin_client, fake_bridge.url)
    assert b["has_key"] and b["key_hint"] == "…WXYZ" and b["kind"] == "arena_local_bridge"
    assert b["terms"]["acknowledged"] is False and b["status"] == "untested" and b["is_mock"] is False
    listing = admin_client.get(BASE).json()
    assert KEY not in json.dumps(b) and KEY not in json.dumps(listing)
    assert KEY not in json.dumps(admin_client.get("/api/providers").json())
    p = db.get(Provider, b["id"])
    cred = db.get(ApiCredential, p.credential_id)
    old_id = cred.id
    assert KEY not in cred.encrypted_value and cred.kind == "arena_local_bridge"
    assert not any(KEY in json.dumps(a.details) + a.message for a in db.scalars(select(AuditLog)))
    r = admin_client.put(f"{BASE}/{b['id']}/key", json={"api_key": "another-key-0000-abcd"})
    assert r.status_code == 200 and r.json()["key_hint"] == "…abcd" and "another-key" not in r.text
    db.expire_all()
    assert db.get(ApiCredential, old_id) is None  # the replaced secret is deleted, not kept
    assert db.get(Provider, b["id"]).credential_id not in (None, old_id)


def test_defaults_and_settings(admin_client, fake_bridge):
    listing = admin_client.get(BASE).json()
    assert listing["default_url"] == "http://host.docker.internal:20140"
    b = _create(admin_client, fake_bridge.url)
    s = b["settings"]
    assert s["stream"] is True and s["replay_window_s"] == 120 and s["max_concurrent"] == 1
    assert s["host_header"] == "" and s["effective_host_header"] is None  # a loopback address needs no override
    r = admin_client.patch(f"{BASE}/{b['id']}", json={"settings": {"stream": False, "request_timeout_s": 300}})
    assert r.status_code == 200 and r.json()["settings"]["stream"] is False
    assert r.json()["settings"]["request_timeout_s"] == 300
    for bad in ({"host_header": "evil.example:80"}, {"nonsense": 1}, {"request_timeout_s": -5},
                {"stream": "yes"}, {"max_concurrent": "many"}):
        r = admin_client.patch(f"{BASE}/{b['id']}", json={"settings": bad})
        assert r.status_code == 422, bad
    docker = _create(admin_client, "http://host.docker.internal:20140")
    assert docker["settings"]["effective_host_header"] == "127.0.0.1:20140"  # what the bridge's host guard accepts


@pytest.mark.parametrize("url,code", [
    ("http://example.com:20140", "host_not_allowed"), ("http://8.8.8.8:20140", "host_not_allowed"),
    ("http://169.254.169.254", "host_not_allowed"), ("https://arena.ai", "host_not_allowed"),
    ("http://user:pw@127.0.0.1:20140", "invalid_url"), ("http://127.0.0.1:20140/admin", "invalid_url"),
    ("file:///etc/passwd", "invalid_url"),
])
def test_only_local_addresses_are_accepted(admin_client, url, code):
    r = admin_client.post(BASE, json={"name": f"Bad {uuid.uuid4().hex[:6]}", "base_url": url, "api_key": KEY})
    assert r.status_code == 422 and r.json()["detail"] == code


def test_duplicate_name_and_unknown_fields(admin_client, fake_bridge):
    b = _create(admin_client, fake_bridge.url)
    r = admin_client.post(BASE, json={"name": b["name"], "base_url": fake_bridge.url, "api_key": KEY})
    assert r.status_code == 409 and r.json()["detail"] == "name_taken"
    r = admin_client.post(BASE, json={"name": "x", "base_url": fake_bridge.url, "api_key": KEY, "is_mock": False})
    assert r.status_code == 422


def test_bridges_are_not_managed_through_the_generic_provider_routes(admin_client, fake_bridge):
    r = admin_client.post("/api/providers", json={"name": "Sneaky", "kind": "arena_local_bridge",
                                                  "base_url": "http://example.com", "api_key": KEY})
    assert r.status_code == 409 and r.json()["detail"] == "managed_at:/api/workforce/bridges"
    b = _create(admin_client, fake_bridge.url)
    assert admin_client.patch(f"/api/providers/{b['id']}", json={"base_url": "http://example.com"}).status_code == 409
    assert admin_client.put(f"/api/providers/{b['id']}/credential", json={"api_key": "x" * 12}).status_code == 409
    assert admin_client.delete(f"/api/providers/{b['id']}").status_code == 409
    kinds = [k["kind"] for k in admin_client.get("/api/provider-kinds").json()]
    assert "arena_local_bridge" not in kinds


# ---------------- who may do what ----------------
def test_only_the_super_admin_configures_bridges_and_acknowledges_terms(admin_client, fake_bridge, make_user):
    b = _create(admin_client, fake_bridge.url)
    statement = b["terms"]["statement"]
    make_user("wf-orgadmin", "ORGANIZATION_ADMIN")
    make_user("wf-developer", "DEVELOPER")
    make_user("wf-viewer", "VIEWER")
    with fresh() as org_admin:
        login_as(org_admin, "wf-orgadmin", PW)
        # an organization admin configures workers but not the bridge, its key or the terms
        assert org_admin.get(BASE).status_code == 200
        assert org_admin.post(BASE, json={"name": "n", "base_url": fake_bridge.url, "api_key": KEY}).status_code == 403
        assert org_admin.patch(f"{BASE}/{b['id']}", json={"enabled": False}).status_code == 403
        assert org_admin.put(f"{BASE}/{b['id']}/key", json={"api_key": "x" * 12}).status_code == 403
        assert org_admin.post(f"{BASE}/{b['id']}/terms", json={"statement": statement}).status_code == 403
        assert org_admin.delete(f"{BASE}/{b['id']}").status_code == 403
        assert org_admin.post(f"{BASE}/{b['id']}/test").status_code == 200  # diagnostics send nothing to Arena
        assert org_admin.post(f"{BASE}/{b['id']}/discover").status_code == 200
    for name in ("wf-developer", "wf-viewer"):
        with fresh() as user:
            login_as(user, name, PW)
            assert user.get(BASE).status_code == 200 and KEY not in user.get(BASE).text
            assert user.post(f"{BASE}/{b['id']}/test").status_code == 403
            assert user.post(f"{BASE}/{b['id']}/terms", json={"statement": statement}).status_code == 403
    assert admin_client.get(f"{BASE}/{b['id']}").json()["terms"]["acknowledged"] is False


def test_other_organizations_cannot_see_or_touch_a_bridge(admin_client, fake_bridge, make_user, db):
    b = _create(admin_client, fake_bridge.url)
    # the strongest role, in another organization: every permission check passes, so only tenancy can refuse
    other_admin = make_user("wf-other-admin", "SUPER_ADMIN", org_slug="other-org")
    try:
        with fresh() as other:
            login_as(other, "wf-other-admin", PW)
            assert b["id"] not in [x["id"] for x in other.get(BASE).json()["bridges"]]
            assert other.get(f"{BASE}/{b['id']}").status_code == 404
            assert other.patch(f"{BASE}/{b['id']}", json={"enabled": False}).status_code == 404
            assert other.put(f"{BASE}/{b['id']}/key", json={"api_key": "x" * 12}).status_code == 404
            for action in ("test", "health", "discover"):
                assert other.post(f"{BASE}/{b['id']}/{action}").status_code == 404
            r = other.post(f"{BASE}/{b['id']}/terms", json={"statement": b["terms"]["statement"]})
            assert r.status_code == 404
            assert other.delete(f"{BASE}/{b['id']}").status_code == 404
    finally:  # the shared test database must keep exactly one Super Admin (test_foundation)
        other_admin.role_id = db.scalar(select(Role.id).where(Role.key == "VIEWER"))
        db.commit()
    after = admin_client.get(f"{BASE}/{b['id']}")
    assert after.status_code == 200 and after.json()["enabled"] is True and after.json()["has_key"] is True
    assert not fake_bridge.bridge.requests  # nothing was sent on the other organization's behalf


def test_generic_provider_routes_reject_a_bridge_id_that_is_not_a_bridge(admin_client):
    mock = next(p for p in admin_client.get("/api/providers").json() if p["kind"] == "mock")
    assert admin_client.get(f"{BASE}/{mock['id']}").status_code == 404
    assert admin_client.post(f"{BASE}/{mock['id']}/terms", json={"statement": "x"}).status_code == 404


# ---------------- terms gate ----------------
def test_terms_need_the_exact_statement_and_are_audited_and_revocable(admin_client, fake_bridge, db):
    b = _create(admin_client, fake_bridge.url)
    statement = b["terms"]["statement"]
    assert fake_bridge.url in statement and "not an official Arena API" in statement
    assert b["terms"]["terms_url"].startswith("https://")
    for wrong in ("I agree", statement.replace("my own accounts", "any accounts"), ""):
        r = admin_client.post(f"{BASE}/{b['id']}/terms", json={"statement": wrong})
        assert r.status_code == 422 and r.json()["detail"] == "statement_mismatch"
    assert admin_client.get(f"{BASE}/{b['id']}").json()["terms"]["acknowledged"] is False
    acked = _ack(admin_client, b)
    assert acked["terms"]["acknowledged"] and acked["terms"]["by"].startswith("user:") and acked["terms"]["at"]
    log = db.scalar(select(AuditLog).where(AuditLog.action == "workforce.bridge_terms_acknowledged")
                    .order_by(AuditLog.id.desc()))
    assert log.details["provider_id"] == b["id"] and log.category == "security" and log.user_id
    r = admin_client.delete(f"{BASE}/{b['id']}/terms")
    assert r.status_code == 200 and r.json()["terms"]["acknowledged"] is False
    assert db.scalar(select(AuditLog).where(AuditLog.action == "workforce.bridge_terms_revoked")) is not None


def test_changing_the_address_revokes_the_acknowledgement(admin_client, fake_bridge):
    b = _ack(admin_client, _create(admin_client, fake_bridge.url))
    r = admin_client.patch(f"{BASE}/{b['id']}", json={"name": b["name"] + " renamed"})
    assert r.json()["terms"]["acknowledged"] is True  # a new name is still the same bridge
    r = admin_client.patch(f"{BASE}/{b['id']}", json={"base_url": "http://host.docker.internal:20140"})
    assert r.status_code == 200 and r.json()["terms"]["acknowledged"] is False
    assert "host.docker.internal:20140" in r.json()["terms"]["statement"] and r.json()["status"] == "untested"


# ---------------- diagnostics ----------------
def test_diagnostics_walk_the_path_and_never_touch_arena(admin_client, fake_bridge, db):
    b = _create(admin_client, fake_bridge.url)
    r = admin_client.post(f"{BASE}/{b['id']}/test")
    assert r.status_code == 200, r.text
    report = r.json()
    steps = _steps(report)
    assert [s["step"] for s in report["steps"]] == ["address", "dns", "tcp", "health", "key", "ready", "models",
                                                    "session_registry", "terms"]
    assert report["ok"] is True and report["ready"] is True and report["mock"] is True
    assert all(steps[k]["ok"] for k in ("dns", "tcp", "health", "key", "ready", "models", "session_registry"))
    assert steps["terms"]["ok"] is False and "acknowledge" in steps["terms"]["hint"]  # reported, not a failure
    assert "FAKE" in steps["health"]["detail"] and steps["session_registry"]["distinct_accounts"] == 4
    assert report["environment"]["base_url"] == fake_bridge.url and report["environment"]["streaming"] is True
    # nothing reached a session, the key-leaking route was not called, and no e-mail address came back
    assert not fake_bridge.bridge.turns and fake_bridge.bridge.status_hits == 0
    assert not any(a in r.text for a in ACCOUNTS) and KEY not in r.text
    after = report["bridge"]
    assert after["status"] == "ok" and after["is_mock"] is True and after["health"]["ready"] is True
    assert db.get(Provider, b["id"]).is_mock is True


def test_diagnostics_name_the_failing_step(admin_client, fake_bridge):
    # 1) nothing listens on the port
    b = _create(admin_client, f"http://127.0.0.1:{_closed_port()}")
    report = admin_client.post(f"{BASE}/{b['id']}/test").json()
    steps = _steps(report)
    assert report["ok"] is False and steps["dns"]["ok"] and steps["tcp"]["ok"] is False
    assert "start Arena Local Bridge" in steps["tcp"]["hint"] and "health" not in steps
    assert report["bridge"]["status"] == "error" and report["bridge"]["health"]["error"]["code"] == "unreachable"
    # 2) a name that does not resolve
    b = _create(admin_client, "http://host.docker.internal:20140")
    report = admin_client.post(f"{BASE}/{b['id']}/test").json()
    steps = _steps(report)
    assert report["ok"] is False and (steps["dns"]["ok"] is False or steps["tcp"]["ok"] is False)
    assert "host.docker.internal" in (steps["dns"]["hint"] or "") or steps["tcp"]["ok"] is False
    # 3) reachable, wrong key
    b = _create(admin_client, fake_bridge.url)
    admin_client.put(f"{BASE}/{b['id']}/key", json={"api_key": "wrong-key-000000000"})
    report = admin_client.post(f"{BASE}/{b['id']}/test").json()
    steps = _steps(report)
    assert report["ok"] is False and steps["health"]["ok"] and steps["key"]["ok"] is False
    assert steps["key"]["code"] == "invalid_key" and "ARENA_AGENT_BRIDGE_KEY" in steps["key"]["hint"]
    assert "ready" not in steps
    # 4) reachable and authenticated, but no account is signed in
    fake_bridge.bridge.ready_accounts.clear()
    b = _create(admin_client, fake_bridge.url)
    report = admin_client.post(f"{BASE}/{b['id']}/test").json()
    steps = _steps(report)
    assert report["ok"] is True and report["ready"] is False  # the connection works; the bridge is not ready
    assert steps["ready"]["ok"] is False and "sign in" in steps["ready"]["hint"]
    assert report["bridge"]["status"] == "error" and "no Arena account is ready" in report["bridge"]["last_error"]


def test_diagnostics_warn_about_localhost_inside_a_container(admin_client, fake_bridge, monkeypatch):
    monkeypatch.setenv("ACC_IN_DOCKER", "1")
    b = _create(admin_client, fake_bridge.url)
    report = admin_client.post(f"{BASE}/{b['id']}/test").json()
    warn = _steps(report)["container_localhost"]
    assert warn["ok"] is None and "host.docker.internal" in warn["hint"]
    assert report["environment"]["in_container"] is True and admin_client.get(BASE).json()["in_container"] is True


# ---------------- health, accounts, discovery ----------------
def test_health_snapshot_masks_accounts_and_maps_sessions_to_owners(admin_client, fake_bridge, db):
    b = _create(admin_client, fake_bridge.url)
    r = admin_client.post(f"{BASE}/{b['id']}/health")
    assert r.status_code == 200, r.text
    h = r.json()["health"]
    assert h["reachable"] and h["ready"] and h["mock"] and h["version"] == "fake-1.1.0" and h["sessions_read"]
    assert [a["account"] for a in h["accounts"]] == ["o***@accounts.example.test"] * 4
    assert len({a["hash"] for a in h["accounts"]}) == 4  # told apart without storing the address
    owners = {s["session_id"]: s["account_hash"] for s in h["sessions"]}
    assert owners[session_id(1)] == h["accounts"][0]["hash"] and owners[session_id(4)] == h["accounts"][3]["hash"]
    assert h["queue"] == {"active": 0, "depth": 0, "max_depth": 8}
    stored = json.dumps(db.get(Provider, b["id"]).health)
    assert not any(a in stored for a in ACCOUNTS) and not any(a in r.text for a in ACCOUNTS)
    assert "FAKE first prompt" not in stored  # prompts in the bridge's registry are not kept
    assert r.json()["health_fresh"] is True
    # a disabled account and its error text are reported; an e-mail inside the error is masked too
    fake_bridge.bridge.accounts[1].update(disabled=True, lastError=f"login failed for {ACCOUNTS[1]}")
    h = admin_client.post(f"{BASE}/{b['id']}/health").json()["health"]
    assert h["accounts"][1]["disabled"] is True and ACCOUNTS[1] not in json.dumps(h)


def test_account_binding_classification_follows_the_bridge_rules(admin_client, fake_bridge):
    b = _create(admin_client, fake_bridge.url)
    h = admin_client.post(f"{BASE}/{b['id']}/health").json()["health"]
    pinned = bridge_service.classify_binding(h, session_id(2))
    assert pinned["type"] == "deterministic_session" and pinned["attested"] is False
    assert pinned["account"] == "o***@accounts.example.test" and pinned["account_hash"] == h["accounts"][1]["hash"]
    assert pinned["account_usable"] is True
    pooled = bridge_service.classify_binding(h, "agent")
    assert pooled["type"] == "pooled" and pooled["accounts"] == 4 and pooled["attested"] is False
    assert bridge_service.classify_binding(h, "active")["type"] == "pooled"
    unknown = bridge_service.classify_binding(h, str(uuid.uuid4()))
    assert unknown == {**unknown, "type": "unknown", "reason": "session_not_found"}
    assert bridge_service.classify_binding(None, session_id(1))["reason"] == "bridge_unreachable"
    # one enabled account left: a non-session model can only run on it
    for a in fake_bridge.bridge.accounts[1:]:
        a["disabled"] = True
    h = admin_client.post(f"{BASE}/{b['id']}/health").json()["health"]
    single = bridge_service.classify_binding(h, "agent")
    assert single["type"] == "deterministic_single_account" and single["account_hash"] == h["accounts"][0]["hash"]
    dead = bridge_service.classify_binding(h, session_id(3))
    assert dead["type"] == "deterministic_session" and dead["account_usable"] is False
    # the registry may be switched off: then a session's owner is honestly unknown
    admin_client.patch(f"{BASE}/{b['id']}", json={"settings": {"read_session_registry": False}})
    h = admin_client.post(f"{BASE}/{b['id']}/health").json()["health"]
    assert h["sessions_read"] is False
    assert bridge_service.classify_binding(h, session_id(1))["reason"] == "session_registry_not_read"


def test_discovery_lists_sessions_with_their_owner(admin_client, fake_bridge, db):
    b = _create(admin_client, fake_bridge.url)
    r = admin_client.post(f"{BASE}/{b['id']}/discover")
    assert r.status_code == 200, r.text
    found = r.json()
    assert found["discovered"] == 4 and found["added"] == 4 and found["mock"] is True
    first = found["models"][0]
    assert first == {**first, "id": session_id(1), "kind": "session", "model": "fake-model-1",
                     "account": "o***@accounts.example.test", "state": "ok"}
    assert "ping" not in [m["id"] for m in found["models"]] and not any(a in r.text for a in ACCOUNTS)
    names = set(db.scalars(select(ModelInfo.name).where(ModelInfo.provider_id == b["id"])))
    assert names == {session_id(i) for i in range(1, 5)}
    assert admin_client.post(f"{BASE}/{b['id']}/discover").json()["added"] == 0  # idempotent
    admin_client.put(f"{BASE}/{b['id']}/key", json={"api_key": "wrong-key-000000000"})
    r = admin_client.post(f"{BASE}/{b['id']}/discover")
    assert r.status_code == 502 and r.json()["detail"] == "invalid_key"


def test_system_health_reports_bridges_without_details(admin_client, fake_bridge):
    b = _create(admin_client, fake_bridge.url)
    admin_client.post(f"{BASE}/{b['id']}/health")
    with fresh() as anonymous:
        r = anonymous.get("/api/health")  # no sign-in needed: so no address, no account, no version
    entry = r.json()["services"]["arena_local_bridge"]
    assert entry["configured"] >= 1 and entry["status"] in ("ok", "degraded", "unreachable", "unknown")
    assert set(entry) == {"status", "configured", "reachable", "ready", "mock"}
    assert str(fake_bridge.port) not in r.text and "accounts.example.test" not in r.text


def test_delete_removes_the_bridge_and_its_key(admin_client, fake_bridge, db):
    b = _create(admin_client, fake_bridge.url)
    cred_id = db.get(Provider, b["id"]).credential_id
    assert admin_client.delete(f"{BASE}/{b['id']}").status_code == 204
    db.expire_all()
    assert db.get(Provider, b["id"]) is None and db.get(ApiCredential, cred_id) is None
    assert admin_client.get(f"{BASE}/{b['id']}").status_code == 404


# ---------------- background health readings ----------------
async def test_monitor_reads_enabled_bridges_and_records_changes(admin_client, fake_bridge, db):
    from app.connectivity.arena_bridge import monitor

    live = _create(admin_client, fake_bridge.url)
    off = _create(admin_client, fake_bridge.url)
    admin_client.patch(f"{BASE}/{off['id']}", json={"enabled": False})
    assert await monitor.poll_once() >= 1
    db.expire_all()
    p = db.get(Provider, live["id"])
    assert p.status == "ok" and p.health["ready"] and p.health["key_ok"] is True and p.is_mock is True
    assert db.get(Provider, off["id"]).health in (None, {})  # a disabled bridge is left alone
    assert not fake_bridge.bridge.turns and fake_bridge.bridge.status_hits == 0  # readings only; nothing to Arena
    # the bridge loses its accounts: the next reading says so and the change is on record
    fake_bridge.bridge.ready_accounts.clear()
    await monitor.poll_once()
    db.expire_all()
    p = db.get(Provider, live["id"])
    assert p.status == "error" and p.health["ready"] is False and "no Arena account is ready" in p.last_error
    change = db.scalar(select(AuditLog).where(AuditLog.action == "workforce.bridge_health_changed")
                       .order_by(AuditLog.id.desc()))
    assert change.details["provider_id"] == live["id"] and change.details["ready"] is False
    # a wrong key is caught by the reading too, although /health itself needs no key
    fake_bridge.bridge.ready_accounts.update(a.lower() for a in ACCOUNTS)
    admin_client.put(f"{BASE}/{live['id']}/key", json={"api_key": "wrong-key-000000000"})
    await monitor.poll_once()
    db.expire_all()
    p = db.get(Provider, live["id"])
    assert p.status == "error" and p.health["key_ok"] is False and p.last_error.startswith("invalid_key")


async def test_monitor_loop_has_one_leader_and_stops_cleanly(admin_client, fake_bridge, db, monkeypatch):
    import asyncio

    from sqlalchemy import text

    from app.connectivity.arena_bridge import monitor
    from app.db import get_engine

    b = _create(admin_client, fake_bridge.url)
    calls: list[int] = []
    real = monitor.poll_once

    async def counted() -> int:
        calls.append(1)
        return await real()

    monkeypatch.setattr(monitor, "poll_once", counted)
    # another process already leads: this one must stay passive
    with get_engine().connect() as holder:
        assert holder.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": monitor.LEADER_KEY}).scalar()
        holder.commit()
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run(stop))
        await asyncio.sleep(0.5)
        stop.set()
        await asyncio.wait_for(task, 10)
        assert calls == []
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": monitor.LEADER_KEY})
        holder.commit()
    stop = asyncio.Event()
    task = asyncio.create_task(monitor.run(stop))
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert len(calls) == 1  # one reading, then it waits for the interval — and leaves at once when asked to stop
    db.expire_all()
    assert db.get(Provider, b["id"]).health["reachable"] is True
