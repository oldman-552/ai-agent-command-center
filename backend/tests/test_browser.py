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
    assert admin_client.post("/api/browser-bridge/sites", json={
        "key": "mychat", "name": "My chat", "url": "https://chat.example.com/app",
        "terms_url": "https://chat.example.com/terms"}).status_code == 201
    ov = admin_client.get("/api/browser-bridge").json()
    assert ov["enabled"] is False and ov["experimental"] is True
    assert {p["key"]: p["unlocked"] for p in ov["providers"]}["mychat"] is False
    r = admin_client.post("/api/browser-bridge/sessions", json={"provider": "test", "profile": "t1"})
    assert r.status_code == 403 and r.json()["detail"] == "bridge_disabled"

    assert admin_client.put("/api/browser-bridge/enabled", json={"enabled": True}).json()["enabled"] is True
    r = admin_client.post("/api/browser-bridge/sessions", json={"provider": "mychat"})
    assert r.status_code == 403 and r.json()["detail"] == "terms_not_acknowledged"
    bad = admin_client.post("/api/browser-bridge/providers/mychat/acknowledge", json={"statement": "ok"})
    assert bad.status_code == 422 and bad.json()["detail"] == "statement_mismatch"
    statement = next(p["statement"] for p in ov["providers"] if p["key"] == "mychat")
    st = admin_client.post("/api/browser-bridge/providers/mychat/acknowledge", json={"statement": statement}).json()
    assert st["acks"]["mychat"]["by"] == "user:1"
    db.expire_all()
    gate.check(db, 1, "mychat")  # unlocked now
    admin_client.delete("/api/browser-bridge/providers/mychat/acknowledge")
    db.expire_all()
    with pytest.raises(gate.GateClosed):
        gate.check(db, 1, "mychat")
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.action.like("browser_bridge.%"))))
    assert {"browser_bridge.enabled", "browser_bridge.terms_acknowledged", "browser_bridge.terms_revoked"} <= actions

    # browser_bridge.manage is human-only and not held by developers
    make_user("bb-dev", "DEVELOPER")
    client.headers.pop("x-csrf-token", None)
    login_as(client, "bb-dev", "user-password-123")
    assert client.put("/api/browser-bridge/enabled", json={"enabled": False}).status_code == 403
    assert client.post("/api/browser-bridge/providers/mychat/acknowledge", json={"statement": statement}).status_code == 403


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
        B.profile_dir(1, name)
    p = B.profile_dir(1, "work-1")
    assert p.is_relative_to(Path(get_settings().browser_profiles_dir).resolve())


def test_bridge_code_has_no_path_to_cookies_or_storage():
    """ADR-025 + ADR-040: only the bridge's session-cookie keeper touches cookies, and only inside the browser. Nothing
    else reads cookies, storage or runs page scripts, and the cookie keeper never stores or logs a value."""
    import app.browser.api as api_mod
    import app.browser.bridge as bridge_mod
    import app.browser.engine as engine_mod
    import app.browser.server as server_mod
    for mod in (api_mod, engine_mod, server_mod):
        src = inspect.getsource(mod)
        for forbidden in (".cookies(", "storage_state", "add_cookies", "local_storage", "evaluate("):
            assert forbidden not in src, (mod.__name__, forbidden)
    src = inspect.getsource(bridge_mod)
    assert "storage_state" not in src and "local_storage" not in src and "evaluate(" not in src
    keeper = inspect.getsource(bridge_mod.Bridge._persist_session_cookies)
    assert "log." not in keeper.replace("log.debug(\"sign-in save skipped: window not available\")", "")
    assert "print(" not in keeper and "json" not in keeper


async def test_a_sign_in_kept_by_a_session_cookie_survives_closing_the_window(db, page_server):
    """ADR-040: a session cookie (no expiry) is written into the profile while the window is open, so it is still
    there when the window is closed and opened again."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    bridge.save_every = 0.05
    s = _session(db, profile="signed-in-1")
    try:
        assert (await bridge.start(s.id))["state"] == "READY"
        ctx = bridge._rt[s.id].context
        host = page_server.split("//", 1)[1].split(":")[0]
        await ctx.add_cookies([{"name": "sign_in", "value": "yes", "domain": host, "path": "/"}])  # session cookie
        await asyncio.sleep(0.3)  # the keeper runs
        await bridge.stop(s.id)
        assert (await bridge.start(s.id))["state"] == "READY"
        names = {c["name"] for c in await bridge._rt[s.id].context.cookies()}
        assert "sign_in" in names, "the sign-in was lost when the window was closed"
        await bridge.stop(s.id)
    finally:
        await bridge.shutdown()


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


async def test_unanswered_prompt_means_interface_changed(db, page_server, monkeypatch):
    monkeypatch.setattr(B, "SEND_WAIT_S", 2)
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id, {"test_mode": "mute"})
        snap = await bridge.send(s.id, "anyone there?")
        assert (snap["state"], snap["error"]) == ("FAILED", "interface_changed")
    finally:
        await bridge.shutdown()


async def test_a_site_that_sends_on_enter_gets_enter(db, page_server):
    """No usable Send button (a rich editor): the prompt still goes out, once."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id, {"test_mode": "enter"})
        await bridge.send(s.id, "sent with enter")
        await _until(bridge, s.id, {"COMPLETED"})
        db.expire_all()
        assert db.get(BrowserSession, s.id).history[-1]["text"] == "Echo: sent with enter"
        assert await bridge._rt[s.id].page.locator(".msg.user").count() == 1  # sent once, not twice
    finally:
        await bridge.shutdown()


async def test_a_person_pressing_send_by_hand_is_waited_for(db, page_server):
    """Send cannot be pressed by the bridge: the prompt waits in the box and the answer is picked up as soon as a
    person presses Send in the open window (owner report, 2026-11-12)."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id, {"test_mode": "manual"})
        page = bridge._rt[s.id].page

        async def person():
            await asyncio.sleep(1.5)
            await page.locator("#send").evaluate("b => { b.disabled = false; }")  # TEST ONLY: the person's click
            await page.locator("#send").click()
        helper = asyncio.create_task(person())
        snap = await bridge.send(s.id, "waiting for a person")
        await helper
        assert snap["state"] == "GENERATING"
        await _until(bridge, s.id, {"COMPLETED"})
        db.expire_all()
        assert db.get(BrowserSession, s.id).history[-1]["text"] == "Echo: waiting for a person"
    finally:
        await bridge.shutdown()


async def test_a_failed_window_that_is_still_open_can_be_used_again(db, page_server, monkeypatch):
    monkeypatch.setattr(B, "SEND_WAIT_S", 1)
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        await bridge.start(s.id, {"test_mode": "mute"})
        assert (await bridge.send(s.id, "lost"))["state"] == "FAILED"
        assert (await bridge.recheck(s.id))["state"] == "READY"  # a person looked: the window is usable again
    finally:
        await bridge.shutdown()


def test_only_a_conversation_on_the_sites_own_https_address_is_reopened():
    same = B.Bridge._same_site
    assert same("https://arena.ai/c/0b9d-42", "https://arena.ai/agent")
    assert not same("http://arena.ai/c/1", "https://arena.ai/agent")
    assert not same("https://evil.example/c/1", "https://arena.ai/agent")
    assert not same("https://arena.ai/" + "x" * 300, "https://arena.ai/agent")


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


async def test_selector_overrides_apply_from_the_next_start_and_are_audited(db, page_server, admin_client):
    """A provider's page changed: the Super Admin adjusts its selectors; the next session uses them."""
    from app.models import AuditLog
    _enable(db)
    gate.set_selectors(db, 1, 1, "test", {"input": "#no-such-box"})  # what a changed page looks like to the bridge
    db.commit()
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        snap = await bridge.start(s.id)
        assert (snap["state"], snap["error"]) == ("FAILED", "interface_changed")
        await bridge.stop(s.id)
        gate.set_selectors(db, 1, 1, "test", {"input": "textarea#prompt, #prompt"})  # the corrected selector
        db.commit()
        assert (await bridge.start(s.id))["state"] == "READY"
        snap = await bridge.send(s.id, "hello")
        assert snap["state"] in ("READY", "GENERATING")
    finally:
        await bridge.shutdown()
    ov = admin_client.get("/api/browser-bridge").json()
    t = next(p for p in ov["providers"] if p["key"] == "test")
    assert t["overridden"] and t["verified"] is False and t["selectors"]["input"] == "textarea#prompt, #prompt"
    assert t["default_selectors"]["input"] == "#prompt"
    audit = db.query(AuditLog).filter(AuditLog.action == "browser_bridge.selectors_changed").order_by(
        AuditLog.id.desc()).first()
    assert audit.details["selectors"] == {"input": "textarea#prompt, #prompt"}
    r = admin_client.put("/api/browser-bridge/providers/test/selectors", json={"selectors": {}})
    assert r.status_code == 200 and "test" not in r.json()["selectors"]  # reset to the built-in selectors
    assert admin_client.put("/api/browser-bridge/providers/test/selectors",
                            json={"selectors": {"cookie": "x"}}).status_code == 422


# ---------------- ADR-032: Arena is not a Browser Bridge site ----------------
def test_arena_is_the_built_in_visible_profile_site_and_custom_arena_sites_stay_refused(admin_client, db):
    """ADR-035 (owner decision): Arena is the built-in `arena` site, driven in a visible profile signed in by hand;
    it is locked until its Arena-specific terms are acknowledged. A custom site on Arena's hosts would bypass that."""
    from app.browser.adapters import ARENA_RETIRED, BUILTIN, catalog, is_arena_url, lookup
    assert set(BUILTIN) == {"test", "arena"} and not BUILTIN["arena"].verified and BUILTIN["arena"].requires_terms
    ov = admin_client.get("/api/browser-bridge").json()
    assert "arena" in {p["key"] for p in ov["providers"]}
    admin_client.put("/api/browser-bridge/enabled", json={"enabled": True})
    r = admin_client.post("/api/browser-bridge/sessions", json={"provider": "arena"})
    assert r.status_code == 403 and r.json()["detail"] == "terms_not_acknowledged"  # the gate answers first
    r = admin_client.post("/api/browser-bridge/providers/arena/acknowledge", json={"statement": "anything"})
    assert r.status_code == 422 and r.json()["detail"] == "statement_mismatch"
    assert "section 5(vi)" in gate.statement("arena")  # the clause that prohibits automation is named
    r = admin_client.post("/api/browser-bridge/providers/arena/acknowledge", json={"statement": gate.statement("arena")})
    assert r.status_code == 200, r.text
    for url in ("https://arena.ai/agent", "https://www.arena.ai/", "https://ARENA.AI./x", "https://lmarena.ai/",
                "https://chat.lmarena.ai/?mode=direct"):
        assert is_arena_url(url), url
        r = admin_client.post("/api/browser-bridge/sites", json={"key": "sneaky", "name": "Sneaky", "url": url})
        assert r.status_code == 422 and r.json()["detail"] == ARENA_RETIRED, url
    for url in ("https://notarena.ai/", "https://arena.ai.example.com/", "https://example.com/arena.ai"):
        assert not is_arena_url(url), url
    # a custom Arena site stored by an older installation is neither listed nor usable (the gate and the bridge both
    # refuse it); the built-in site is the way
    org = db.get(Organization, 1)
    db.refresh(org)
    cfg = dict(org.settings["browser_bridge"])
    cfg["sites"] = {"oldarena": {"name": "Old", "url": "https://arena.ai/agent"}}
    cfg["acks"] = {"oldarena": {"by": "user:1"}}
    org.settings = {**org.settings, "browser_bridge": cfg}
    db.commit()
    assert "oldarena" not in catalog(org.settings, include_hidden=True) and lookup(org.settings, "oldarena") is None
    with pytest.raises(gate.GateClosed) as e:
        gate.check(db, 1, "oldarena")
    assert e.value.code == ARENA_RETIRED
    s = _session(db, provider="oldarena")

    async def start():
        with pytest.raises(B.BridgeError) as be:
            await B.Bridge(base_url="http://127.0.0.1:9", poll=0.05).start(s.id)
        return be.value
    err = asyncio.run(start())
    assert err.code == ARENA_RETIRED and err.status == 403


def test_migration_retires_stored_arena_data_and_is_reversible(db):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    path = Path(__file__).resolve().parent.parent / "alembic/versions/b1a7e0a3c032_retire_arena_browser_site.py"
    spec = importlib.util.spec_from_file_location("retire_arena", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    before = {"enabled": True, "acks": {"arena": {"by": "user:1"}, "mychat": {"by": "user:1"}, "mine": {"by": "user:1"}},
              "selectors": {"arena": {"input": "#x"}},
              "sites": {"mine": {"name": "Mine", "url": "https://www.arena.ai/agent"},
                        "mychat": {"name": "Chat", "url": "https://chat.example.com"}},
              "profiles": {"emp-1": {"label": "Employee 1"}}}
    after, keys = mig.retire(before)
    assert keys == ["arena", "mine"]
    assert set(after["acks"]) == {"mychat"} and after["selectors"] == {} and set(after["sites"]) == {"mychat"}
    assert after["profiles"] == before["profiles"] and after["enabled"] is True  # nothing else is touched
    assert set(after["retired_arena"]["acks"]) == {"arena", "mine"} and "mine" in after["retired_arena"]["sites"]
    assert mig.restore(after) == before  # reversible
    assert mig.retire({"enabled": False, "acks": {}})[0] == {"enabled": False, "acks": {}}  # nothing to retire: unchanged

    org = db.get(Organization, 1)
    org.settings = {**(org.settings or {}), "browser_bridge": before}
    live = BrowserSession(organization_id=1, provider_key="arena", profile_name="emp-1", state="READY", history=[])
    other = BrowserSession(organization_id=1, provider_key="mychat", profile_name="emp-2", state="READY", history=[])
    db.add_all([live, other])
    db.commit()
    engine = create_engine(get_settings().database_url)
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()
        db.expire_all()
        cfg = db.get(Organization, 1).settings["browser_bridge"]
        assert "arena" not in cfg["acks"] and "mine" not in cfg["sites"] and "mychat" in cfg["sites"]
        assert cfg["retired_arena"]["sessions"][str(live.id)] == {"state": "READY", "error": None}
        assert db.get(BrowserSession, live.id).state == "STOPPED" and "ADR-032" in db.get(BrowserSession, live.id).error
        assert db.get(BrowserSession, other.id).state == "READY"  # other sites are untouched
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mig.upgrade()  # running it again changes nothing
        db.expire_all()
        assert db.get(Organization, 1).settings["browser_bridge"] == cfg
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            mig.downgrade()
        db.expire_all()
        assert db.get(Organization, 1).settings["browser_bridge"] == before
        assert db.get(BrowserSession, live.id).error is None
    finally:
        engine.dispose()
        other.state = "STOPPED"
        db.commit()


# ---------------- sites the Super Admin adds, profiles per person ----------------
def test_arena_is_the_only_real_built_in_site_and_the_test_page_is_hidden_by_default(admin_client, monkeypatch):
    monkeypatch.delenv("BROWSER_SHOW_TEST_PAGE", raising=False)
    keys = [p["key"] for p in admin_client.get("/api/browser-bridge").json()["providers"]]
    assert keys == ["arena"]  # ADR-035: Arena in a visible profile; every other site is one the Super Admin added
    monkeypatch.setenv("BROWSER_SHOW_TEST_PAGE", "true")
    assert {p["key"] for p in admin_client.get("/api/browser-bridge").json()["providers"]} == {"test", "arena"}


def test_super_admin_adds_any_site_which_stays_locked_until_its_terms_are_acknowledged(admin_client, db):
    from app.models import AuditLog
    bad = admin_client.post("/api/browser-bridge/sites", json={"key": "x1", "name": "X", "url": "http://plain.example"})
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_url"  # https only
    assert admin_client.post("/api/browser-bridge/sites", json={"key": "arena", "name": "X",
                             "url": "https://x.example"}).json()["detail"] == "invalid_site_key"
    r = admin_client.post("/api/browser-bridge/sites", json={
        "key": "mychat", "name": "My chat", "url": "https://chat.example.com/app",
        "terms_url": "https://chat.example.com/terms", "input": "#box", "response": ".answer"})
    assert r.status_code == 201, r.text
    ov = admin_client.get("/api/browser-bridge").json()
    site = next(p for p in ov["providers"] if p["key"] == "mychat")
    assert site["custom"] and not site["unlocked"] and not site["verified"]
    assert site["selectors"]["input"] == "#box" and site["url"] == "https://chat.example.com/app"
    admin_client.put("/api/browser-bridge/enabled", json={"enabled": True})
    assert admin_client.post("/api/browser-bridge/sessions", json={"provider": "mychat"}).json()["detail"] == \
        "terms_not_acknowledged"
    assert admin_client.post("/api/browser-bridge/providers/mychat/acknowledge",
                             json={"statement": site["statement"]}).status_code == 200
    db.expire_all()
    gate.check(db, 1, "mychat")
    st = admin_client.delete("/api/browser-bridge/sites/mychat").json()
    assert "mychat" not in st["acks"]  # nothing stays unlocked
    db.expire_all()
    with pytest.raises(gate.GateClosed):
        gate.check(db, 1, "mychat")
    acts = set(db.scalars(select(AuditLog.action).where(AuditLog.action.like("browser_bridge.site_%"))))
    assert acts == {"browser_bridge.site_added", "browser_bridge.site_removed"}


def test_each_person_and_agent_has_a_profile_and_a_profile_opens_in_one_window(admin_client, db):
    _enable(db)
    bridge_client.LOCAL = B.Bridge(base_url="http://127.0.0.1:9", poll=0.05)  # never actually launched below
    assert admin_client.post("/api/browser-bridge/profiles", json={"name": "Bad Name"}).status_code == 422
    r = admin_client.post("/api/browser-bridge/profiles", json={"name": "acct-1", "label": "Account 1"})
    assert r.status_code == 201 and [p["name"] for p in r.json()] == ["acct-1"]
    from app.models import Agent
    a = Agent(organization_id=1, name="Researcher", identifier=f"res-{uuid.uuid4().hex[:6]}", role="research",
              state="IDLE", execution_engine="browser")
    db.add(a)
    db.commit()
    from app.browser import api as bapi
    from app.models import User
    admin = db.get(User, 1)
    assert bapi.own_profile(db, admin, None) == "admin"
    assert bapi.own_profile(db, admin, a) == f"agent-{a.identifier}"
    db.commit()
    profs = {p["name"]: p for p in admin_client.get("/api/browser-bridge/profiles").json()}
    assert profs["admin"]["owner"] == "user:1" and profs[f"agent-{a.identifier}"]["owner"] == f"agent:{a.id}"
    assert bapi.profile_name_for("أحمد") == bapi.profile_name_for("أحمد") and bapi.profile_name_for("أحمد").startswith("p-")
    # one open window per profile, whatever the site
    live = BrowserSession(organization_id=1, provider_key="test", profile_name="acct-1", state="READY", history=[])
    db.add(live)
    db.commit()
    r = admin_client.post("/api/browser-bridge/sessions", json={"provider": "test", "profile": "acct-1"})
    assert r.status_code == 409 and r.json()["detail"] == "profile_in_use"
    assert admin_client.delete("/api/browser-bridge/profiles/acct-1").status_code == 409
    live.state = "STOPPED"
    db.commit()
    assert "acct-1" not in [p["name"] for p in admin_client.delete("/api/browser-bridge/profiles/acct-1").json()]
    assert admin_client.post("/api/browser-bridge/sessions",
                             json={"provider": "test", "profile": "nobody"}).json()["detail"] == "unknown_profile"


async def test_a_profile_is_shared_across_sites_and_deleting_it_removes_its_sign_ins(db, page_server):
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s1 = _session(db, profile="emp-1")
    try:
        assert (await bridge.start(s1.id))["state"] == "READY"
        s2 = _session(db, profile="emp-1")
        with pytest.raises(B.BridgeError) as e:  # the same browser profile cannot open twice
            await bridge.start(s2.id)
        assert e.value.code == "profile_in_use"
        await bridge.stop(s1.id)
        assert (await bridge.start(s2.id))["state"] == "READY"  # same profile directory, another session
        assert B.profile_dir(1, "emp-1").exists()
        await bridge.stop(s2.id)
    finally:
        await bridge.shutdown()
    from app.browser import server as srv
    srv_bridge = srv.get_bridge
    srv.get_bridge = lambda: bridge
    try:
        assert (await srv.delete_profile(1, "emp-1")) == {"deleted": "emp-1"}
    finally:
        srv.get_bridge = srv_bridge
    assert not B.profile_dir(1, "emp-1").exists()


# ---------------- BB-05: page-close detection (ADR-039) ----------------

async def test_manually_closed_window_marks_session_disconnected(db, page_server):
    """Scenario 1: user closes the browser window manually — the bridge detects the page close event and
    transitions the session to DISCONNECTED (not FAILED) so it can be reopened without a manual reset."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        snap = await bridge.start(s.id)
        assert snap["state"] == "READY"
        rt = bridge._rt[s.id]
        # simulate the user closing the browser window: close the page directly
        await rt.page.close()
        # wait for the async handler to propagate
        for _ in range(50):
            await asyncio.sleep(0.1)
            db.expire_all()
            row = db.get(B.BrowserSession, s.id)
            if row.state == "DISCONNECTED":
                break
        db.expire_all()
        row = db.get(B.BrowserSession, s.id)
        assert row.state == "DISCONNECTED", f"expected DISCONNECTED, got {row.state}"
        assert s.id not in bridge._rt, "runtime entry should be gone after page close"
    finally:
        await bridge.shutdown()


async def test_page_close_during_sending_marks_session_failed(db, page_server, monkeypatch):
    """Scenario 2 variant: a page closes while in SENDING state → FAILED (not DISCONNECTED)
    so the task owner knows the prompt may not have been delivered."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        snap = await bridge.start(s.id)
        assert snap["state"] == "READY"
        # force DB state to SENDING without going through the full send path
        with bridge._db() as db2:
            row = db2.get(B.BrowserSession, s.id)
            row.state = "SENDING"
            db2.commit()
        rt = bridge._rt[s.id]
        await rt.page.close()
        for _ in range(50):
            await asyncio.sleep(0.1)
            db.expire_all()
            row = db.get(B.BrowserSession, s.id)
            if row.state in ("FAILED", "DISCONNECTED"):
                break
        db.expire_all()
        row = db.get(B.BrowserSession, s.id)
        assert row.state == "FAILED", f"mid-send close should be FAILED, got {row.state}"
    finally:
        await bridge.shutdown()


async def test_new_tabs_opened_in_the_context_also_get_close_handlers(db, page_server):
    """context.on('page') handler: a new tab opened inside the same context should also trigger
    _on_page_gone when that tab is closed, keeping the session in sync."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    s = _session(db)
    try:
        snap = await bridge.start(s.id)
        assert snap["state"] == "READY"
        rt = bridge._rt[s.id]
        # open a new tab in the same context
        new_page = await rt.context.new_page()
        await asyncio.sleep(0.05)
        # closing the new tab should fire our _on_page_gone handler
        await new_page.close()
        for _ in range(50):
            await asyncio.sleep(0.1)
            db.expire_all()
            row = db.get(B.BrowserSession, s.id)
            if row.state == "DISCONNECTED":
                break
        db.expire_all()
        row = db.get(B.BrowserSession, s.id)
        assert row.state == "DISCONNECTED", f"closing new tab should disconnect session, got {row.state}"
    finally:
        await bridge.shutdown()


async def test_shutdown_closes_all_sessions_and_marks_them_disconnected(db, page_server):
    """Scenario 3: ./stop.sh → worker calls bridge.shutdown() → all open pages are closed cleanly
    and all sessions are marked DISCONNECTED so they reopen on next start."""
    _enable(db)
    bridge = B.Bridge(base_url=page_server, poll=0.05)
    sessions = [_session(db) for _ in range(2)]
    try:
        for s in sessions:
            snap = await bridge.start(s.id)
            assert snap["state"] == "READY", f"session {s.id} not READY: {snap}"
        assert len(bridge._rt) == 2
        await bridge.shutdown()
        assert len(bridge._rt) == 0, "all runtimes should be gone after shutdown"
        for s in sessions:
            db.expire_all()
            row = db.get(B.BrowserSession, s.id)
            assert row.state == "DISCONNECTED", f"session {s.id} should be DISCONNECTED after shutdown, got {row.state}"
    finally:
        # already shut down; second shutdown should be a no-op
        await bridge.shutdown()
