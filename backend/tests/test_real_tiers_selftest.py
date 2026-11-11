"""The two real tiers (`test_real_provider.py`, `test_real_arena.py`) cannot run during development: they need the
owner's key, bridge and accounts. This file runs **the same steps** on the mock provider and the FAKE bridge, so
that a typo in a step is found here and not on the owner's machine.

Tier: mock / local integration. Passing here says the steps work; it says nothing about a real model or about Arena.
"""
from __future__ import annotations

import asyncio

import pytest

from devtools.fake_arena_bridge import FakeBridge
from tests import test_real_arena as arena
from tests import test_real_provider as provider
from tests import wf as h
from tests.fakebridge import ACCOUNTS, KEY, FakeBridgeServer, session_id

TWO = """1. Name three features of a to-do list app [research]
2. Write a description of the app in two sentences [documentation] (after 1)"""


# ---------------- real_provider steps, on the mock provider ----------------
def test_the_real_provider_steps_work_on_the_mock_provider(admin_client, db, capsys):
    h.reset(db)
    expect = provider.Expect("mock", "mock-model", is_mock=True)
    out = provider.people_flow(admin_client, db, expect, prompt=TWO)
    assert out["tasks"] == 2 and sorted(out["verdicts"].values()) == ["approve", "approve"]
    assert out["approved_by_operator"] == []
    printed = capsys.readouterr().out
    assert "MOCK (self-test)" in printed and "consolidate" in printed and "report v1" in printed


def test_the_real_provider_steps_let_the_operator_settle_what_the_manager_hands_over(admin_client, db, monkeypatch):
    h.reset(db)
    # a result the mock Manager escalates: the flow must approve it as the operator and still finish
    monkeypatch.setattr(provider, "answer_for", lambda card: "Done. [[mock:review-escalate]]")
    out = provider.people_flow(admin_client, db, provider.Expect("mock", "mock-model", is_mock=True), prompt=TWO)
    assert len(out["approved_by_operator"]) == 2 and set(out["verdicts"].values()) == {"needs_human"}


def test_the_real_provider_steps_fail_loudly_when_the_model_answer_is_unusable(admin_client, db, monkeypatch):
    h.reset(db)
    monkeypatch.setattr(provider, "answer_for", lambda card: "Done. [[mock:review-garbage]]")
    with pytest.raises(AssertionError, match="invalid_verdict"):
        provider.people_flow(admin_client, db, provider.Expect("mock", "mock-model", is_mock=True), prompt=TWO)
    h.reset(db)
    with pytest.raises(AssertionError, match="planning ended"):
        provider.people_flow(admin_client, db, provider.Expect("mock", "mock-model", is_mock=True),
                             prompt="[[mock:plan-garbage]]\n" + TWO)


def test_the_real_provider_steps_notice_a_mock_where_a_real_model_is_expected(admin_client, db):
    h.reset(db)
    with pytest.raises(AssertionError):
        provider.people_flow(admin_client, db, provider.Expect("anthropic", "some-real-model"), prompt=TWO)


def test_the_unattended_steps_work_with_workers_on_the_mock_provider(admin_client, db):
    h.reset(db)
    expect = provider.Expect("mock", "mock-model", is_mock=True)
    out = provider.unattended_flow(admin_client, db, h.mock_provider(db).id, expect, prompt=TWO)
    assert out["executions"] == 2 and out["approved_by_operator"] == []


# ---------------- real_arena steps, on the FAKE bridge ----------------
def test_the_real_arena_steps_work_against_the_fake_bridge(admin_client, db, fake_bridge, capsys):
    h.reset(db)
    b = arena.connect(admin_client, fake_bridge.url, KEY, fake=True)
    try:
        chosen = arena.bind_four_workers(admin_client, b)
        assert [s["id"] for s in chosen] == [session_id(n) for n in (1, 2, 3, 4)]
        assert not fake_bridge.bridge.turns  # connecting, discovering and binding sent nothing to "Arena"
        result = arena.run_four(admin_client, db, b, fake=True)
        assert result["executions"] == 4 and result["retries"] == 0 and result["approved_by_operator"] == []
        assert all(len(fake_bridge.turns(session_id(n))) == 1 for n in (1, 2, 3, 4))  # one brief per session
        rep = asyncio.run(arena.replay(db, b["id"], chosen[0]["id"]))
        assert len(fake_bridge.turns(session_id(1))) == 2  # the random-number prompt once; its repeat was replayed
        arena.show("self-test on the FAKE bridge", b, chosen, result, rep)
        printed = capsys.readouterr().out
        assert "4 tasks completed" in printed and "same text" in printed
        assert KEY not in printed and not any(a in printed for a in ACCOUNTS)  # masked addresses only, never the key
    finally:
        arena.remove(admin_client, db, b["id"])
    assert admin_client.get(f"{arena.BRIDGES}/{b['id']}").status_code == 404


def test_the_real_arena_steps_accept_sessions_named_by_the_owner(admin_client, db, fake_bridge):
    h.reset(db)
    b = arena.connect(admin_client, fake_bridge.url, KEY, fake=True)
    try:
        wanted = [session_id(n) for n in (4, 3, 2, 1)]
        chosen = arena.bind_four_workers(admin_client, b, wanted)
        assert [s["id"] for s in chosen] == wanted
        with pytest.raises(AssertionError, match="sessions not found"):
            arena.bind_four_workers(admin_client, b, ["11111111-1111-4111-8111-111111111111", *wanted[1:]])
        with pytest.raises(AssertionError, match="four different accounts"):
            arena.bind_four_workers(admin_client, b, [wanted[0], wanted[0], wanted[1], wanted[2]])
    finally:
        arena.remove(admin_client, db, b["id"])


def test_the_real_arena_tier_refuses_the_fake_bridge(admin_client, db, fake_bridge):
    h.reset(db)
    with pytest.raises(AssertionError, match="FAKE bridge"):
        arena.connect(admin_client, fake_bridge.url, KEY, fake=False)


def test_the_real_arena_steps_say_what_is_missing(admin_client, db, fake_bridge):
    h.reset(db)
    fake_bridge.bridge.ready_accounts.clear()
    with pytest.raises(AssertionError, match="no account is ready"):
        arena.connect(admin_client, fake_bridge.url, KEY, fake=True)
    with pytest.raises(AssertionError, match="cannot be reached"):
        arena.connect(admin_client, "http://127.0.0.1:9", KEY, fake=True)


def test_fewer_than_four_accounts_is_not_enough(admin_client, db):
    h.reset(db)
    small = FakeBridge(key=KEY, heartbeat_seconds=0.2)
    for i, email in enumerate(ACCOUNTS[:3], start=1):
        small.add_account(email, priority=i)
        small.add_session(email, model=f"fake-model-{i}", title=f"FAKE session {i}", sid=session_id(i))
    srv = FakeBridgeServer(small).start()
    try:
        with pytest.raises(AssertionError, match="four signed-in accounts are needed"):
            arena.connect(admin_client, srv.url, KEY, fake=True)
    finally:
        srv.stop()
