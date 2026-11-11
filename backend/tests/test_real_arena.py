"""WF-20 — Automatic workers on a real Arena Local Bridge with four Arena accounts. Tier: **REAL ARENA**.
Skipped unless you switch it on.

This is the only place where the platform talks to the real bridge: every other test uses the fake bridge in
`devtools/fake_arena_bridge.py`, which was written from the bridge's source code. **Nobody ran this file against a
real bridge during development.** (`test_real_tiers_selftest.py` runs the same steps against the fake bridge, so the
steps themselves are known to work.)

Before you run it:
  1. Arena Local Bridge is running on this computer and four Arena accounts are signed in and verified in it.
  2. Each of the four accounts has one conversation ("session") of its own in the bridge.
  3. You have read Arena's terms of use. The bridge is not an official Arena API; the accounts are yours and so is the
     responsibility for using them this way.

Run it — `REAL-TESTS.bat` does this for you, or by hand (PowerShell: `$env:NAME = "value"`):

    cd backend
    REAL_ARENA_BRIDGE_URL=http://127.0.0.1:20140 REAL_ARENA_BRIDGE_KEY=<the bridge key> REAL_ARENA_ACCEPT_TERMS=yes \\
        uv run pytest -m real_arena -v -s

    REAL_ARENA_BRIDGE_URL    http://host.docker.internal:20140 when the tests run in a container (REAL-TESTS.bat)
    REAL_ARENA_ACCEPT_TERMS  must be exactly "yes": the test acknowledges the platform's terms statement for you
    REAL_ARENA_SESSIONS      optional: four session ids, comma-separated, one per account; otherwise the test picks
                             one session of each of four different accounts from the bridge's registry

What it sends to Arena, through your accounts: five short prompts — one task brief to each of the four sessions
("write one short sentence about …") and one "reply with a random number" prompt to the first session. The second
copy of that last prompt must be answered by the bridge from its cache without reaching Arena.
What stays local: the key is stored encrypted in the **test** database during the run and deleted afterwards; it is
never printed, and account addresses are printed masked only. `-s` shows a summary you can keep as evidence.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from sqlalchemy import select

from app.connectivity.arena_bridge import service as bridge_service
from app.models import Execution, Provider, Task
from tests import wf as h

URL = os.environ.get("REAL_ARENA_BRIDGE_URL", "")
KEY = os.environ.get("REAL_ARENA_BRIDGE_KEY", "")
ACCEPT = os.environ.get("REAL_ARENA_ACCEPT_TERMS", "") == "yes"
SESSIONS = [s.strip() for s in os.environ.get("REAL_ARENA_SESSIONS", "").split(",") if s.strip()]
# REAL-TESTS.bat names the tier it wants. Then missing settings are a failure: a run that silently skipped everything
# would end green and be mistaken for evidence.
REQUIRED = os.environ.get("REAL_TIER_REQUIRED", "") == "real_arena"
API = "/api/workforce"
BRIDGES = f"{API}/bridges"

pytestmark = [
    pytest.mark.real_arena,
    pytest.mark.skipif(not (URL and KEY and ACCEPT) and not REQUIRED,
                       reason="real Arena tier: set REAL_ARENA_BRIDGE_URL, REAL_ARENA_BRIDGE_KEY and "
                              "REAL_ARENA_ACCEPT_TERMS=yes"),
]

# The mock Manager plans these four lines as four independent tasks, one per worker: this tier is about the path to
# Arena, not about the Manager's model (that is the real_provider tier).
FOUR = """1. Write one short sentence about the sea [research] @W1
2. Write one short sentence about the desert [engineering] @W2
3. Write one short sentence about a forest [qa] @W3
4. Write one short sentence about mountains [documentation] @W4"""
RANDOM = "Reply with a random 12-digit number and nothing else."
OPERATOR_NOTE = "approved by the test operator"


def connect(admin_client, url: str, key: str, *, fake: bool) -> dict:
    """Add the bridge and walk the diagnostics. Nothing is sent to Arena yet."""
    r = admin_client.post(BRIDGES, json={"name": f"Real-tier bridge {uuid.uuid4().hex[:6]}", "base_url": url, "api_key": key})
    assert r.status_code == 201, r.text
    assert key not in r.text
    b = r.json()
    report = admin_client.post(f"{BRIDGES}/{b['id']}/test").json()
    steps = {s["step"]: s for s in report["steps"]}
    failed = [f"{s['step']}: {s['detail']} -> {s.get('hint')}" for s in report["steps"] if s["ok"] is False and s["step"] != "terms"]
    assert report["ok"], f"the bridge cannot be reached from here: {failed}"
    assert report["ready"], f"the bridge answers, but no account is ready: {failed}"
    if fake:
        assert report["mock"] is True
    else:
        assert report["mock"] is False, "this address answers as the FAKE bridge; the real tier needs a real Arena Local Bridge"
    enabled = [a for a in steps["ready"]["accounts"] if not a["disabled"]]
    assert len(enabled) >= 4, f"four signed-in accounts are needed, the bridge has {len(enabled)} enabled"
    assert len({a["hash"] for a in enabled}) == len(enabled)
    return b


def bind_four_workers(admin_client, bridge: dict, wanted: list[str] | None = None) -> list[dict]:
    """Acknowledge the terms, read the sessions, and give each worker one session of a different account."""
    bid = bridge["id"]
    r = admin_client.post(f"{BRIDGES}/{bid}/terms", json={"statement": bridge["terms"]["statement"]})
    assert r.status_code == 200, r.text
    assert admin_client.post(f"{BRIDGES}/{bid}/discover").status_code == 200
    assert admin_client.post(f"{BRIDGES}/{bid}/health").status_code == 200
    options = admin_client.get(f"{API}/options").json()
    sessions = next(p for p in options["providers"] if p["id"] == bid)["sessions"]
    assert sessions, "the bridge lists no session: create one conversation per account in the bridge"
    if wanted:
        known = {s["id"].lower(): s for s in sessions}
        missing = [w for w in wanted if w.lower() not in known]
        assert not missing, f"sessions not found in the bridge: {missing}"
        chosen = [known[w.lower()] for w in wanted]
    else:
        per_account: dict[str, dict] = {}
        for s in sessions:
            if s.get("account_hash") and s["account_hash"] not in per_account and (s.get("state") or "ok") == "ok":
                per_account[s["account_hash"]] = s
        chosen = list(per_account.values())[:4]
    assert len(chosen) == 4, f"four sessions are needed, one per account; found {len(chosen)} accounts with a usable session"
    assert len({s["account_hash"] for s in chosen}) == 4, "the four sessions must belong to four different accounts"
    workers = admin_client.get(API).json()["workforce"]["workers"]
    for w, s in zip(workers, chosen, strict=True):
        r = admin_client.patch(f"{API}/workers/{w['id']}", json={"mode": "AUTOMATIC", "provider_id": bid, "model": s["id"]})
        assert r.status_code == 200, r.text
    assert admin_client.post(f"{BRIDGES}/{bid}/health").status_code == 200
    force = admin_client.get(API).json()["workforce"]
    for w in force["workers"]:
        assert w["status"]["state"] == "ready", (w["label"], w["status"])
        assert w["account_binding"]["type"] == "deterministic_session" and w["account_binding"]["attested"] is False, w["account_binding"]
    summary = force["accounts"]
    assert summary["automatic_workers"] == 4 and summary["deterministic"] == 4, summary
    assert summary["distinct_accounts"] == 4 and summary["shared_accounts"] == [], summary
    return chosen


def run_four(admin_client, db, bridge: dict, *, fake: bool) -> dict:
    """One objective, four tasks, one per worker: each brief goes to its own session; every answer comes back, is
    recorded with the bridge's request id and without invented usage, and the objective completes."""
    wf = h.planned(db, FOUR)
    started = time.monotonic()
    h.drive(db, wf, rounds=12)
    by_operator: list[int] = []
    for _ in range(4):  # an answer that looks like an instruction or a secret is held for a person: accept and go on
        waiting = admin_client.get(f"{API}/tasks", params={"objective_id": wf.id, "queue": "review"}).json()["tasks"]
        if not waiting:
            break
        for card in waiting:
            r = admin_client.post(f"{API}/tasks/{card['id']}/review", json={"decision": "approve", "note": OPERATOR_NOTE})
            assert r.status_code == 200, r.text
            by_operator.append(card["id"])
        h.drive(db, wf, rounds=4)
    seconds = round(time.monotonic() - started, 1)
    db.expire_all()
    tasks = list(db.scalars(select(Task).where(Task.workflow_id == wf.id).order_by(Task.id)))
    assert len(tasks) == 4
    rows = []
    for t in tasks:
        card = admin_client.get(f"{API}/tasks/{t.id}").json()
        assert card["lifecycle"] == "COMPLETED", (t.id, card["lifecycle"], card["error"], card["failures"])
        sub = card["submissions"][-1]
        rows.append({"task": t.id, "worker": card["worker"]["label"], "attempts": card["attempts"], "chars": len(sub["content"]),
                     "request_id": sub["provider"]["request_id"], "session": sub["provider"]["model"],
                     "account": sub["provider"]["account_binding"].get("account"),
                     "account_hash": sub["provider"]["account_binding"].get("account_hash"), "sub": sub})
    for row in rows:
        sub = row["sub"]
        assert sub["mode"] == "AUTOMATIC" and sub["source"] == "arena_local_bridge" and sub["content"].strip()
        assert sub["is_mock"] is fake
        assert sub["content"].startswith("[FAKE ARENA BRIDGE") is fake  # a real answer never carries the fake's banner
        assert sub["provider"]["usage_reported"] is False  # the bridge reports no usage: recorded as unknown, not 0
        assert row["request_id"], "the bridge's request id was not recorded"
        assert sub["provider"]["account_binding"]["type"] == "deterministic_session"
    assert [r["worker"] for r in rows] == ["W1", "W2", "W3", "W4"]
    assert len({r["session"] for r in rows}) == 4 and len({r["account_hash"] for r in rows}) == 4
    assert len({r["request_id"] for r in rows}) == 4
    runs = list(db.scalars(select(Execution).join(Task, Task.id == Execution.task_id).where(Task.workflow_id == wf.id)))
    assert runs and all(e.input_tokens is None and e.output_tokens is None for e in runs if e.status == "COMPLETED")
    obj = admin_client.get(f"{API}/objectives/{wf.id}").json()
    assert obj["status"] == "completed", (obj["status"], obj["error"])
    key = db.get(Provider, bridge["id"]).credential
    assert key is not None and admin_client.get(f"{API}/objectives/{wf.id}/history").text.count("Bearer") == 0
    for row in rows:
        row.pop("sub")
    return {"workflow_id": wf.id, "seconds": seconds, "rows": rows, "approved_by_operator": by_operator,
            "executions": len(runs), "retries": sum(max(r["attempts"] - 1, 0) for r in rows)}


async def replay(db, bridge_id: int, session: str) -> dict:
    """The same request twice with the same idempotency key: the bridge must answer the second from its cache. A
    random-number prompt makes the difference visible — a second turn in Arena would return another number."""
    p = db.get(Provider, bridge_id)
    assert bridge_service.acknowledged(p), "nothing is sent before the terms are acknowledged"
    client = bridge_service.client_for(p)
    idem = f"acc-realtier-{uuid.uuid4().hex}"
    try:
        t0 = time.monotonic()
        first = await client.complete(model=session, prompt=RANDOM, idempotency_key=idem)
        t1 = time.monotonic()
        again = await client.complete(model=session, prompt=RANDOM, idempotency_key=idem)
        t2 = time.monotonic()
    finally:
        await client.aclose()
    assert first.text.strip()
    assert again.text == first.text, "the repeated request got a different answer: it reached Arena a second time"
    return {"first_seconds": round(t1 - t0, 2), "replay_seconds": round(t2 - t1, 2), "chars": len(first.text)}


def remove(admin_client, db, bridge_id: int) -> None:
    """Workers back to Human-Assisted, the bridge and its key out of the test database."""
    h.reset(db)
    r = admin_client.delete(f"{BRIDGES}/{bridge_id}")
    assert r.status_code in (204, 404), r.text


def show(title: str, bridge: dict, chosen: list[dict], result: dict | None = None, rep: dict | None = None) -> None:
    print(f"\n=== {title} ===")
    print(f"  bridge: {bridge['base_url']}")
    for i, s in enumerate(chosen, start=1):
        print(f"  W{i}: session {s['id']}  account {s.get('account')} #{(s.get('account_hash') or '')[:4]}")
    if result:
        print(f"  objective #{result['workflow_id']}: 4 tasks completed in {result['seconds']} s; "
              f"retries {result['retries']}; held for a person {len(result['approved_by_operator'])}")
        for r in result["rows"]:
            print(f"    task #{r['task']} {r['worker']}: {r['chars']} characters, bridge request {r['request_id']}")
    if rep:
        print(f"  repeated request: first answer {rep['first_seconds']} s, replay {rep['replay_seconds']} s, same text")


@pytest.fixture()
def real_bridge(admin_client, db):
    assert URL and KEY, "REAL_ARENA_BRIDGE_URL and REAL_ARENA_BRIDGE_KEY did not reach the test: nothing was checked"
    assert ACCEPT, "REAL_ARENA_ACCEPT_TERMS is not 'yes': nothing is sent to Arena without your acknowledgement"
    h.reset(db)
    b = connect(admin_client, URL, KEY, fake=False)
    yield b
    remove(admin_client, db, b["id"])


def test_four_workers_on_four_accounts_each_answer_through_their_own_session(admin_client, db, real_bridge):
    chosen = bind_four_workers(admin_client, real_bridge, SESSIONS or None)
    result = run_four(admin_client, db, real_bridge, fake=False)
    show("REAL ARENA tier — four workers, four accounts", real_bridge, chosen, result)


def test_a_repeated_request_is_answered_by_the_bridge_and_not_sent_to_arena_again(admin_client, db, real_bridge):
    chosen = bind_four_workers(admin_client, real_bridge, SESSIONS or None)
    rep = asyncio.run(replay(db, real_bridge["id"], chosen[0]["id"]))
    show("REAL ARENA tier — repeated request", real_bridge, chosen, rep=rep)
