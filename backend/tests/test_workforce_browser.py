"""ADR-035 — a worker driving its OWN visible browser window (a profile of its own, signed in by hand).

Tier: local integration — the real Browser Bridge (Playwright Chromium) on the platform's own test chat page, the
mock Manager, the real scheduler and runtime. No real site (Arena) is contacted.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.browser import bridge as B
from app.browser import client as bridge_client
from app.models import BrowserSession, Execution, Task
from app.runtime import service as runtime
from app.workforce import api as wf_api
from app.workforce import control, scheduler, service
from tests import wf as h
from tests.test_browser import _bridge_env, _enable, page_server  # noqa: F401  (fixtures)


async def _run_round(db, wf_id: int) -> list[str]:
    await control.tick(wf_id)
    db.expire_all()
    out = []
    for ex in db.scalars(select(Execution).join(Task, Task.id == Execution.task_id).where(
            Task.workflow_id == wf_id, Execution.status == "CREATED")).all():
        await runtime.run_execution(ex.id)
        out.append(await scheduler.on_execution_finished(db, ex.id))
    return out


def test_a_worker_gets_its_own_window_drives_it_and_the_window_stays_open(db, page_server):  # noqa: F811
    h.reset(db)
    _enable(db)  # the bridge is on; the local test site needs no terms

    async def go():
        bridge = B.Bridge(base_url=page_server, poll=0.05)
        bridge_client.LOCAL = bridge
        try:
            w = h.worker(db, 1)
            out = await wf_api.open_worker_browser(w.id, wf_api.WorkerBrowserIn(site="test"), db, h.admin(db))
            b = out["account_binding"]
            assert b["type"] == "browser_profile" and b["site"] == "test" and b["attested"] is False
            agent = h.agent_of(db, 1)
            assert b["profile"] == f"agent-{agent.identifier}"[:40]  # one profile per worker, named after it
            # opening it again reuses the same window (one profile = one window), it does not open a second one
            again = await wf_api.open_worker_browser(w.id, wf_api.WorkerBrowserIn(site="test"), db, h.admin(db))
            assert again["account_binding"]["session_id"] == b["session_id"]
            service.update_worker(db, h.worker(db, 1), user=h.admin(db), patch={"mode": "AUTOMATIC"})
            db.commit()
            assert service.eligibility(db, h.worker(db, 1)).mode == "AUTOMATIC"

            wf = h.objective(db, "1. Summarise the options [research]")
            assert await control.plan(wf.id) == "planned"
            for _ in range(4):
                await _run_round(db, wf.id)
            db.expire_all()
            t = next(iter(h.tasks_of(db, wf).values()))
            sub = h.subs(db, t)
            assert sub and sub[0].mode == "AUTOMATIC" and sub[0].content.startswith("Echo: ")  # typed into the window
            ex = db.scalars(select(Execution).where(Execution.task_id == t.id)).first()
            assert ex.provider == "browser:test" and ex.engine == "browser" and ex.is_mock is False
            s = db.get(BrowserSession, b["session_id"])
            assert s.state in ("READY", "COMPLETED") and s.agent_id == agent.id  # the window stays open to watch
        finally:
            await bridge.shutdown()

    asyncio.run(go())


def test_a_window_that_is_not_ready_hands_the_task_to_a_person_and_nothing_is_typed(db):
    h.reset(db)
    _enable(db)
    s = BrowserSession(organization_id=1, provider_key="test", profile_name="agent-signed-out", state="AUTH_REQUIRED",
                       history=[])
    db.add(s)
    db.commit()
    service.update_worker(db, h.worker(db, 1), user=h.admin(db),
                          patch={"browser_session_id": s.id, "mode": "AUTOMATIC", "fallback_to_human": True})
    db.commit()
    e = service.eligibility(db, h.worker(db, 1))
    assert (e.mode, e.reason) == ("HUMAN_ASSISTED", "fallback:session_unavailable")
    wf = h.planned(db, "1. Summarise the options [research]")
    h.tick(wf)
    t = next(iter(h.tasks_of(db, wf).values()))
    assert t.lifecycle == "WAITING_FOR_ACCEPTANCE"  # a person signs in in the open window, or does the task
    assert db.scalars(select(Execution).where(Execution.task_id == t.id)).first() is None


def test_browser_failures_are_retried_or_handed_to_a_person_never_sent_twice():
    def act(code, category):
        return scheduler.failure_action({"code": code, "category": category}, 1, 3, True)
    assert act("bridge_unreachable", "bridge_unavailable") == "retry"   # nothing reached the window
    assert act("not_ready", "session_unavailable") == "human"         # signed out / bot check: a person fixes it
    assert act("generation_timeout", "outcome_unknown") == "human"    # the prompt was sent: never type it twice


def test_switching_back_to_a_provider_leaves_the_browser(db):
    h.reset(db)
    s = BrowserSession(organization_id=1, provider_key="test", profile_name="agent-switch", state="READY", history=[])
    db.add(s)
    db.commit()
    w = h.worker(db, 2)
    service.update_worker(db, w, user=h.admin(db), patch={"browser_session_id": s.id})
    a = h.agent_of(db, 2)
    assert a.execution_engine == "browser" and a.context_policy["browser_session_id"] == s.id
    service.update_worker(db, w, user=h.admin(db), patch={"provider_id": h.mock_provider(db).id, "model": "mock-model"})
    db.commit()
    a = h.agent_of(db, 2)
    assert a.execution_engine == "api" and "browser_session_id" not in (a.context_policy or {})


# ---------------- ADR-036: the Manager in its own window ----------------
class _FakeWindow:
    """Stands in for the Browser Bridge (TEST ONLY): what the Manager types, and a window that answers like the
    deterministic mock Manager — or is signed out, or never finishes."""

    def __init__(self, db, sid: int, behaviour: str = "answer"):
        self.db, self.sid, self.behaviour, self.typed = db, sid, behaviour, []

    async def call(self, method, path, body=None):
        from app.browser.bridge import BridgeError
        from app.connectivity.gateway.adapters import MOCK_RESPONDERS
        from app.connectivity.gateway.base import ChatRequest
        _TASK_HEADER = "### YOUR TASK"
        if path.endswith("/prompt"):
            if self.behaviour == "signed_out":
                raise BridgeError("auth_required")
            if self.behaviour == "unreachable":
                raise BridgeError("bridge_unreachable")
            self.typed.append(body["prompt"])
            rules, _, task = body["prompt"].partition(_TASK_HEADER)
            text = next(r(ChatRequest(model="x", system=rules, messages=[{"role": "user", "content": task.strip()}]))
                        for m, r in MOCK_RESPONDERS.items() if m in rules)
            s = self.db.get(BrowserSession, self.sid)
            s.history = [*(s.history or []), {"role": "user", "text": "…"}, {"role": "assistant", "text": text}]
            s.state = "GENERATING" if self.behaviour == "hang" else "COMPLETED"
            self.db.commit()
            return {"state": s.state}
        if path.endswith("/cancel"):
            return {"state": "READY"}
        return {"state": "GENERATING" if self.behaviour == "hang" else "COMPLETED"}


def _manager_window(db, monkeypatch, behaviour="answer"):
    from app.browser import client as bclient
    s = BrowserSession(organization_id=1, provider_key="test", profile_name=f"agent-mgr-{behaviour}", state="READY",
                       history=[])
    db.add(s)
    db.commit()
    force = service.get(db, 1)
    service.update_manager(db, force, user=h.admin(db), patch={"browser_session_id": s.id})
    db.commit()
    fake = _FakeWindow(db, s.id, behaviour)
    monkeypatch.setattr(bclient, "call", fake.call)
    return fake, force


def test_the_manager_plans_through_its_own_window(db, monkeypatch):
    from app.models import Agent, ManagerStep
    h.reset(db)
    fake, force = _manager_window(db, monkeypatch)
    out = service.manager_out(db, force)
    assert out["configured"] and out["engine"] == "browser" and out["window"]["site"] == "test" and out["via_bridge"]
    wf = h.objective(db, "1. Summarise the options [research]\n2. Write it up [documentation] (after 1)")
    assert asyncio.run(control.plan(wf.id)) == "planned"
    assert len(h.tasks_of(db, wf)) == 2
    steps = db.scalars(select(ManagerStep).where(ManagerStep.workflow_id == wf.id)).all()
    assert {s.provider for s in steps} == {"browser"} and all(s.status == "ok" for s in steps)
    assert len(fake.typed) == len(steps) and all("### YOUR TASK" in p for p in fake.typed)  # rules travel with it
    # choosing an API model again leaves the window
    service.update_manager(db, force, user=h.admin(db), patch={"provider_id": h.mock_provider(db).id, "model": "mock-model"})
    db.commit()
    m = db.get(Agent, force.manager_agent_id)
    assert m.execution_engine == "api" and "browser_session_id" not in (m.context_policy or {})


def test_a_signed_out_manager_window_stops_planning_for_a_person(db, monkeypatch):
    h.reset(db)
    fake, _ = _manager_window(db, monkeypatch, "signed_out")
    wf = h.objective(db, "1. Summarise the options [research]")
    assert asyncio.run(control.plan(wf.id)) == "failed"
    db.expire_all()
    assert "manager_window_failed" in db.get(type(wf), wf.id).error and fake.typed == []


def test_a_manager_window_that_never_finishes_is_not_typed_into_twice(db, monkeypatch):
    from app.models import Agent
    h.reset(db)
    fake, force = _manager_window(db, monkeypatch, "hang")
    m = db.get(Agent, force.manager_agent_id)
    m.timeout_seconds = 30
    db.commit()
    from app.workforce import manager
    ticks = iter(range(0, 10_000, 40))
    monkeypatch.setattr(manager, "_clock", lambda: next(ticks))
    monkeypatch.setattr(manager, "WINDOW_POLL_S", 0)
    wf = h.objective(db, "1. Summarise the options [research]")
    assert asyncio.run(control.plan(wf.id)) == "failed"
    assert len(fake.typed) == 1  # typed once; the timeout stops the step instead of sending it again


def test_an_unreachable_bridge_falls_back_to_the_managers_api_model(db, monkeypatch):
    from app.models import Agent
    h.reset(db)
    fake, force = _manager_window(db, monkeypatch, "unreachable")
    m = db.get(Agent, force.manager_agent_id)
    m.fallback_provider_id, m.fallback_model = h.mock_provider(db).id, "mock-model"
    db.commit()
    wf = h.objective(db, "1. Summarise the options [research]")
    assert asyncio.run(control.plan(wf.id)) == "planned" and fake.typed == []


# ---------------- ADR-037: team readiness and "move everyone to their own window" ----------------
def test_readiness_names_every_missing_piece_and_moving_everyone_fixes_them(db, page_server, monkeypatch):  # noqa: F811
    from app.config import get_settings
    from app.workforce import readiness
    h.reset(db)
    monkeypatch.setattr(get_settings(), "browser_max_sessions", 10)

    async def go():
        bridge = B.Bridge(base_url=page_server, poll=0.05)
        bridge_client.LOCAL = bridge
        try:
            # nothing set up: the bridge is off, nobody has a window
            r = await readiness.readiness(db, service.get(db, 1))
            assert r["ready"] is False and r["checks"]["bridge_enabled"] is False
            assert {m["label"]: m["problem"] for m in r["members"]}["W1"] in ("no_connection", "old_connection")
            # moving everyone while the bridge is off stops at the first answer, opens nothing
            out = await wf_api.open_all_windows(wf_api.AllWindowsIn(site="test"), db, h.admin(db))
            assert out["results"][0]["ok"] is False and len(out["results"]) == 1
            _enable(db)
            service.update_worker(db, h.worker(db, 4), user=h.admin(db), patch={"mode": "DISABLED"})
            db.commit()
            out = await wf_api.open_all_windows(wf_api.AllWindowsIn(site="test"), db, h.admin(db))
            assert [x["label"] for x in out["results"]] == ["Manager", "W1", "W2", "W3"]  # Disabled W4 left alone
            assert all(x["ok"] for x in out["results"]), out["results"]
            sessions = {x["session_id"] for x in out["results"]}
            assert len(sessions) == 4  # one window per member, never shared
            db.expire_all()
            r = out["readiness"]
            assert r["checks"]["bridge_enabled"] and r["checks"]["bridge_running"]
            members = {m["label"]: m for m in r["members"]}
            assert all(members[x]["connection"] == "window" and members[x]["problem"] is None
                       for x in ("Manager", "W1", "W2", "W3"))
            assert members["W4"]["problem"] is None and members["W4"]["mode"] == "DISABLED"
            assert service.eligibility(db, h.worker(db, 1)).mode == "AUTOMATIC"
            # doing it again reuses the same windows
            again = await wf_api.open_all_windows(wf_api.AllWindowsIn(site="test"), db, h.admin(db))
            assert {x["session_id"] for x in again["results"]} == sessions
        finally:
            await bridge.shutdown()

    asyncio.run(go())


def test_readiness_tells_a_signed_out_window_from_a_closed_one(db):
    from app.workforce import readiness
    h.reset(db)
    _enable(db)
    for slot, state in ((1, "AUTH_REQUIRED"), (2, "STOPPED")):
        s = BrowserSession(organization_id=1, provider_key="test", profile_name=f"agent-ready-{slot}", state=state,
                           history=[])
        db.add(s)
        db.commit()
        service.update_worker(db, h.worker(db, slot), user=h.admin(db), patch={"browser_session_id": s.id})
    db.commit()
    bridge_client.LOCAL = None
    r = asyncio.run(readiness.readiness(db, service.get(db, 1)))
    m = {x["label"]: x["problem"] for x in r["members"]}
    assert m["W1"] == "sign_in" and m["W2"] == "window_closed" and r["ready"] is False
