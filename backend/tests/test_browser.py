"""M9 — Browser Bridge (EXPERIMENTAL): gate (BB-03), state machine against a local test page (BB-02), security (BB-04),
and the `browser` execution engine. Uses the real pre-installed Chromium through Playwright, headless."""
from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from sqlalchemy import select

import app.main  # noqa: F401
from app.browser import bridge as B
from app.browser import client as bridge_client
from app.browser import gate
from app.browser.api import TEST_PAGE
from app.config import get_settings
from app.models import Agent, AuditLog, BrowserSession, Execution, Organization
from app.runtime import service


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = TEST_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def page_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _bridge_env(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "browser_profiles_dir", str(tmp_path / "profiles"))
    monkeypatch.setattr(get_settings(), "browser_generation_timeout_seconds", 20)
    org = db.get(Organization, 1)
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "browser_bridge"}
    db.commit()
    yield
    bridge_client.LOCAL = None
    org = db.get(Organization, 1)
    db.refresh(org)
    org.settings = {k: v for k, v in (org.settings or {}).items() if k != "browser_bridge"}
    db.commit()


def _enable(db, *providers):
    gate.set_enabled(db, 1, 1, True)
    for p in providers:
        gate.acknowledge(db, 1, 1, p, gate.statement(p))
    db.commit()


def _session(db, provider="test", profile=None) -> BrowserSession:
    s = BrowserSession(organization_id=1, provider_key=provider, profile_name=profile or f"p-{uuid.uuid4().hex[:8]}",
                       state="DISCONNECTED", history=[])
    db.add(s)
    db.commit()
    return s


async def _until(bridge, sid, states, timeout=15):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        snap = bridge.snapshot(sid)
        if snap["state"] in states:
            return snap
        await asyncio.sleep(0.1)
    raise AssertionError(f"session {sid} stuck in {bridge.snapshot(sid)}")


# ---------------- BB-03: the gate ----------------
def test_disabled_by_default_and_per_provider_terms_gate(admin_client, make_user, client, db):
    from tests.conftest import login_as
    ov = admin_client.get("/api/browser-bridge").json()
    assert ov["enabled"] is False and ov["experimental"] is True
    assert {p["key"]: p["unlocked"] for p in ov["providers"]}["claude"] is False
    r = admin_client.post("/api/browser-bridge/sessions", json={"provider": "test", "profile": "t1"})
    assert r.status_code == 403 and r.json()["detail"] == "bridge_disabled"

    assert admin_client.put("/api/browser-bridge/enabled", json={"enabled": True}).json()["enabled"] is True
    r = admin_client.post("/api/browser-bridge/sessions", json={"provider": "claude", "profile": "work"})
    assert r.status_code == 403 and r.json()["detail"] == "terms_not_acknowledged"
    bad = admin_client.post("/api/browser-bridge/providers/claude/acknowledge", json={"statement": "ok"})
    assert bad.status_code == 422 and bad.json()["detail"] == "statement_mismatch"
    statement = next(p["statement"] for p in ov["providers"] if p["key"] == "claude")
    st = admin_client.post("/api/browser-bridge/providers/claude/acknowledge", json={"statement": statement}).json()
    assert st["acks"]["claude"]["by"] == "user:1"
    db.expire_all()
    gate.check(db, 1, "claude")  # unlocked now
    admin_client.delete("/api/browser-bridge/providers/claude/acknowledge")
    db.expire_all()
    with pytest.raises(gate.GateClosed):
        gate.check(db, 1, "claude")
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.action.like("browser_bridge.%"))))
    assert {"browser_bridge.enabled", "browser_bridge.terms_acknowledged", "browser_bridge.terms_revoked"} <= actions

    # browser_bridge.manage is human-only and not held by developers
    make_user("bb-dev", "DEVELOPER")
    client.headers.pop("x-csrf-token", None)
    login_as(client, "bb-dev", "user-password-123")
    assert client.put("/api/browser-bridge/enabled", json={"enabled": False}).status_code == 403
    assert client.post("/api/browser-bridge/providers/claude/acknowledge", json={"statement": statement}).status_code == 403


def test_transition_table_is_enforced(db):
    s = _session(db)
    with pytest.raises(B.BridgeError):
        B.transition(db, s, "GENERATING")  # DISCONNECTED cannot jump to GENERATING
    B.transition(db, s, "STARTING")
    B.transition(db, s, "READY")
    with pytest.raises(B.BridgeError):
        B.transition(db, s, "COMPLETED")
    assert set(B.TRANSITIONS) == {"DISCONNECTED", "STARTING", "AUTH_REQUIRED", "READY", "SENDING", "GENERATING",
                                  "COMPLETED", "FAILED", "STOPPED"}
    db.rollback()


# ---------------- BB-04: profiles and secrets ----------------
@pytest.mark.parametrize("name", ["../escape", "Default", "a/b", "", ".hidden", "x" * 41, "/home/u/.config/chrome"])
def test_only_dedicated_profile_directories(name):
    with pytest.raises(B.BridgeError):
        B.profile_dir(1, "test", name)
    p = B.profile_dir(1, "test", "work-1")
    assert p.is_relative_to(Path(get_settings().browser_profiles_dir).resolve())


def test_bridge_code_has_no_path_to_cookies_or_storage():
    import app.browser.api as api_mod
    import app.browser.bridge as bridge_mod
    import app.browser.engine as engine_mod
    import app.browser.server as server_mod
    for mod in (bridge_mod, api_mod, engine_mod, server_mod):
        src = inspect.getsource(mod)
        for forbidden in (".cookies(", "storage_state", "add_cookies", "local_storage", "evaluate("):
            assert forbidden not in src, (mod.__name__, forbidden)


# ---------------- BB-02: full lifecycle against the local test page ----------------
async def test_lifecycle_prompt_stream_cancel_stop_restart(db, page_server):
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        snap = await bridge.start(s.id)
        assert snap["state"] == "READY" and snap["live"]
        snap = await bridge.send(s.id, "hello bridge")
        assert snap["state"] == "GENERATING"
        done = await _until(bridge, s.id, {"COMPLETED"})
        assert done["error"] is None
        db.expire_all()
        hist = db.get(BrowserSession, s.id).history
        assert [h["role"] for h in hist] == ["user", "assistant"] and hist[1]["text"] == "Echo: hello bridge"

        await bridge.send(s.id, "second turn")
        await _until(bridge, s.id, {"COMPLETED"})
        db.expire_all()
        assert db.get(BrowserSession, s.id).history[-1]["text"] == "Echo: second turn"

        with pytest.raises(B.BridgeError):
            await bridge.cancel(s.id)  # nothing to cancel

        # conversation reset, then the session keeps working
        await bridge.new_conversation(s.id)
        db.expire_all()
        assert db.get(BrowserSession, s.id).history == []

        assert (await bridge.stop(s.id))["state"] == "STOPPED"
        with pytest.raises(B.BridgeError):
            await bridge.send(s.id, "after stop")
        assert (await bridge.start(s.id))["state"] == "READY"  # reconnection from STOPPED
    finally:
        await bridge.shutdown()


async def test_cancel_during_generation(db, page_server, monkeypatch):
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id, {"test_mode": "slow"})
        await bridge.send(s.id, "a long answer that streams slowly")
        await asyncio.sleep(0.6)
        assert bridge.snapshot(s.id)["partial"]  # streaming: partial text is visible while generating
        snap = await bridge.cancel(s.id)
        assert snap["state"] == "READY"
        db.expire_all()
        last = db.get(BrowserSession, s.id).history[-1]
        assert last["cancelled"] is True and last["text"].startswith("Echo")
        assert last["text"] != "Echo: a long answer that streams slowly"
    finally:
        await bridge.shutdown()


@pytest.mark.parametrize(("mode", "state", "error"), [
    ("login", "AUTH_REQUIRED", "login_required"),
    ("captcha", "AUTH_REQUIRED", "captcha_detected"),
    ("broken", "FAILED", "interface_changed"),
])
async def test_auth_captcha_and_interface_changes_fail_safely(db, page_server, mode, state, error):
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        snap = await bridge.start(s.id, {"test_mode": mode})
        assert (snap["state"], snap["error"]) == (state, error)
        if mode == "captcha":  # the bridge waits for a human; it never touches the challenge
            rt = bridge._rt[s.id]
            assert await rt.page.locator("iframe[data-captcha]").count() == 1
            with pytest.raises(B.BridgeError):
                await bridge.send(s.id, "x")
            assert (await bridge.recheck(s.id))["state"] == "AUTH_REQUIRED"
        if mode == "login":  # a human signs in in the bridge window, then re-check
            await bridge._rt[s.id].page.locator("#sign-in").click()
            await bridge._rt[s.id].page.wait_for_load_state()
            assert (await bridge.recheck(s.id))["state"] == "READY"
    finally:
        await bridge.shutdown()


async def test_unanswered_prompt_means_interface_changed(db, page_server):
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id, {"test_mode": "mute"})
        snap = await bridge.send(s.id, "anyone there?")
        assert (snap["state"], snap["error"]) == ("FAILED", "interface_changed")
    finally:
        await bridge.shutdown()


async def test_gate_rechecked_on_every_prompt_and_no_secret_leaves(db, page_server):
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id)
        await bridge.send(s.id, "first")
        snap = await _until(bridge, s.id, {"COMPLETED"})
        # the page set a cookie; nothing the bridge reports contains it
        db.expire_all()
        everything = repr(snap) + repr(db.get(BrowserSession, s.id).history) + repr(bridge.health())
        assert "TOPSECRET" not in everything and "cookie" not in everything.lower()
        gate.set_enabled(db, 1, 1, False)
        db.commit()
        with pytest.raises(B.BridgeError) as e:
            await bridge.send(s.id, "after disable")
        assert e.value.code == "bridge_disabled"
    finally:
        await bridge.shutdown()


async def test_session_limit(db, page_server, monkeypatch):
    _enable(db)
    monkeypatch.setattr(get_settings(), "browser_max_sessions", 1)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    a, b = _session(db), _session(db)
    try:
        await bridge.start(a.id)
        with pytest.raises(B.BridgeError) as e:
            await bridge.start(b.id)
        assert e.value.code == "too_many_sessions"
    finally:
        await bridge.shutdown()


async def test_orphaned_sessions_are_disconnected_on_restart(db):
    s = _session(db)
    s.state = "GENERATING"
    db.commit()
    assert B.Bridge.mark_orphans_disconnected() >= 1
    db.expire_all()
    assert db.get(BrowserSession, s.id).state == "DISCONNECTED"


# ---------------- the experimental `browser` engine ----------------
def test_browser_engine_runs_an_agent_turn(db, page_server):
    _enable(db)

    async def go():
        bridge = B.Bridge(base_url=page_server, poll=0.05)
        bridge_client.LOCAL = bridge
        s = _session(db)
        try:
            await bridge.start(s.id)
            a = Agent(organization_id=1, name=f"Web {uuid.uuid4().hex[:5]}", identifier=f"web-{uuid.uuid4().hex[:8]}",
                      role="backend", state="IDLE", execution_engine="browser",
                      context_policy={"browser_session_id": s.id})
            db.add(a)
            db.commit()
            ex = service.create_execution(db, organization_id=1, agent=a, input={"prompt": "via engine"},
                                          actor="user:1")
            db.commit()
            await service.run_execution(ex.id)
            return ex.id, s.id
        finally:
            await bridge.shutdown()

    ex_id, _sid = asyncio.run(go())
    db.expire_all()
    ex = db.get(Execution, ex_id)
    assert ex.status == "COMPLETED", ex.error
    assert ex.output["text"] == "Echo: via engine" and ex.provider == "browser:test" and ex.is_mock is False
