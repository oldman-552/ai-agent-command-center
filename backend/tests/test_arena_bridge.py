"""WF-03 — Arena Local Bridge client, gateway adapter and API engine, against the FAKE bridge over real HTTP.

Tier: local integration (real sockets, real SSE, real PostgreSQL). The fake reproduces the bridge's documented
behaviour from its source; nothing here talks to Arena or to the real bridge, and no result of this file is evidence
of a real Arena call (see `real_arena` tests for that tier).
"""
import asyncio
import socket
import uuid

import httpx2 as httpx
import pytest
from sqlalchemy import select

from app.connectivity.arena_bridge.adapter import RULES_HEADER, TASK_HEADER, ArenaLocalBridgeAdapter, fold_prompt
from app.connectivity.arena_bridge.client import (
    ArenaBridgeClient,
    BridgeConfig,
    BridgeError,
    account_hash,
    host_allowed,
    mask_email,
    normalize_base_url,
)
from app.connectivity.gateway.base import ChatRequest, ProviderError, ProviderUnavailable
from app.models import Agent, CostRecord, Execution, ExecutionEvent, Provider, UsageRecord
from tests.fakebridge import ACCOUNTS, KEY, FakeBridgeServer, four_account_bridge, session_id

S1 = session_id(1)
TERMS = {"terms_ack": {"by": "user:1", "at": "2026-01-01T00:00:00+00:00"}}


def cfg(srv, **kw) -> BridgeConfig:
    return BridgeConfig(base_url=srv.url, terms_acknowledged=True, **kw)


async def chat(client: ArenaBridgeClient, **kw):
    return [item async for item in client.chat(**kw)]


async def failure(client: ArenaBridgeClient, **kw) -> BridgeError:
    with pytest.raises(BridgeError) as e:
        await chat(client, **kw)
    return e.value


# ---------------- addresses ----------------
@pytest.mark.parametrize("raw,expected", [
    ("http://127.0.0.1:20140", "http://127.0.0.1:20140"),
    ("http://host.docker.internal:20140/", "http://host.docker.internal:20140"),
    ("http://host.docker.internal:20140/v1", "http://host.docker.internal:20140"),
    ("http://localhost", "http://localhost:80"),
    ("http://[::1]:20140", "http://[::1]:20140"),
])
def test_base_url_is_normalised(raw, expected):
    assert normalize_base_url(raw) == expected


@pytest.mark.parametrize("raw", ["ftp://127.0.0.1:20140", "http://user:pw@127.0.0.1:20140", "127.0.0.1:20140",
                                 "http://127.0.0.1:20140/some/prefix", "http://127.0.0.1:20140?x=1", ""])
def test_malformed_or_credential_bearing_urls_are_refused(raw):
    with pytest.raises(ValueError):
        normalize_base_url(raw)


@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("host.docker.internal", True), ("192.168.1.20", True),
    ("10.0.0.5", True), ("172.17.0.1", True), ("example.com", False), ("8.8.8.8", False), ("169.254.169.254", False),
    ("arena.ai", False), ("metadata.google.internal", False), ("fd00::1", False),
])
def test_only_local_and_private_hosts_may_be_a_bridge(host, ok):
    assert host_allowed(host) is ok
    assert host_allowed("bridge.lan", {"bridge.lan"})  # an operator-supplied name is accepted explicitly


def test_account_emails_are_masked_and_hashed():
    assert mask_email("someone@example.com") == "s***@example.com"
    assert "someone" not in mask_email("someone@example.com")
    assert account_hash("Someone@Example.com", "salt") == account_hash("someone@example.com ", "salt")
    assert account_hash("someone@example.com", "salt") != account_hash("someone@example.com", "other")


# ---------------- unauthenticated routes, discovery, probe ----------------
async def test_health_ready_sessions_models_and_ping(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    h = await c.health()
    assert h["ok"] and h["ready"] and h["version"] == "fake-1.1.0"
    assert h["_fake"] is True  # the fake always identifies itself; the platform labels it mock
    ready, _ = await c.ready()
    assert ready is True
    rows = await c.sessions()
    assert [r["session_id"] for r in rows] == [session_id(i) for i in range(1, 5)]
    assert set(rows[0]) == {"session_id", "model", "title", "email", "state"}  # prompts and local paths are dropped
    assert rows[0]["email"] == ACCOUNTS[0] and rows[0]["state"] == "ok"
    models = await c.models()
    assert {m["id"] for m in models} == {*[session_id(i) for i in range(1, 5)], "ping"}
    assert next(m for m in models if m["id"] == S1)["owned_by"] == "arena-session"
    pong = await c.ping()
    assert pong.text == "pong" and pong.request_id and pong.bridge_version == "fake-1.1.0"
    await c.aclose()
    # the key travels only to /v1/* routes, and the key-leaking /api/status route is never called
    assert all(r["auth"] == r["path"].startswith("/v1") for r in fake_bridge.bridge.requests)
    assert fake_bridge.bridge.status_hits == 0
    assert not fake_bridge.bridge.turns  # nothing above reached a "session"


async def test_ready_reports_a_bridge_without_a_usable_account(fake_bridge):
    fake_bridge.bridge.ready_accounts.clear()
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    ready, body = await c.ready()
    assert ready is False and body["ready"] is False
    assert (await c.health())["ok"] is True  # /health stays 200: the process is alive
    await c.aclose()


async def test_host_header_is_derived_for_non_loopback_addresses_and_the_guard_is_reported():
    srv = FakeBridgeServer(four_account_bridge(), host="0.0.0.0").start()
    try:
        url = f"http://127.0.0.2:{srv.port}"  # not one of the three names the bridge accepts
        c = ArenaBridgeClient(BridgeConfig(base_url=url), KEY)
        assert c.cfg.effective_host_header == f"127.0.0.1:{srv.port}"
        assert (await c.health())["ok"]
        assert srv.bridge.requests[-1]["host"] == f"127.0.0.1:{srv.port}"
        await c.aclose()
        bad = ArenaBridgeClient(BridgeConfig(base_url=url, host_header="bridge.example:20140"), KEY)
        with pytest.raises(BridgeError) as e:
            await bad.health()
        assert e.value.code == "host_guard" and e.value.category == "configuration" and not e.value.retryable
        assert "127.0.0.1" in e.value.message
        await bad.aclose()
    finally:
        srv.stop()


async def test_loopback_addresses_keep_their_own_host_header(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    assert c.cfg.effective_host_header is None
    await c.health()
    assert fake_bridge.bridge.requests[-1]["host"] == f"127.0.0.1:{fake_bridge.port}"
    await c.aclose()


async def test_a_service_that_is_not_the_bridge_is_recognised():
    other = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"hello": "world"})))
    c = ArenaBridgeClient(BridgeConfig(base_url="http://127.0.0.1:20140"), KEY, other)
    with pytest.raises(BridgeError) as e:
        await c.health()
    assert e.value.code == "not_a_bridge" and e.value.category == "invalid_response" and not e.value.retryable


async def test_unreachable_bridge_is_retryable_and_nothing_was_delivered():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again right away: nothing listens here
    c = ArenaBridgeClient(BridgeConfig(base_url=f"http://127.0.0.1:{port}", terms_acknowledged=True,
                                       connect_timeout=1.0), KEY)
    with pytest.raises(BridgeError) as e:
        await c.health()
    assert e.value.code == "unreachable" and e.value.category == "bridge_unavailable" and e.value.retryable
    err = await failure(c, model=S1, prompt="hello")
    assert err.code == "unreachable" and err.as_dict()["delivered"] is False
    await c.aclose()


# ---------------- chat ----------------
async def test_streaming_chat_yields_text_heartbeats_and_correlation(fake_bridge):
    fake_bridge.bridge.chunk_chars = 4000
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    items = await chat(c, model=S1, prompt="Summarise the design [[fake:slow:0.7]]", session_id="acc-worker-1",
                       idempotency_key="exec-1-r0")
    kinds = [k for k, _ in items]
    assert kinds[0] == "meta" and kinds[-1] == "result" and "delta" in kinds
    meta, res = items[0][1], items[-1][1]
    assert meta["provider_request_id"] and meta["bridge_version"] == "fake-1.1.0" and meta["mock"] is True
    assert res.text.startswith("[FAKE ARENA BRIDGE — not Arena]") and "Summarise the design" in res.text
    assert res.request_id == meta["provider_request_id"] and res.finish_reason == "stop" and res.fake
    assert res.heartbeats >= 2  # the stream stayed open while the turn ran; empty deltas are not text
    assert "".join(v for k, v in items if k == "delta") == res.text
    sent = fake_bridge.completions()[-1]
    assert sent["session"] == "acc-worker-1" and sent["idempotency"] == "exec-1-r0" and sent["auth"]
    assert fake_bridge.turns(S1) == ["Summarise the design [[fake:slow:0.7]]"]
    await c.aclose()


async def test_non_streaming_chat(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge, stream=False), KEY)
    res = await c.complete(model=S1, prompt="One line please")
    assert "One line please" in res.text and res.chunks == 1 and res.request_id and res.fake
    await c.aclose()


async def test_a_named_retry_of_a_finished_request_is_replayed_not_sent_again(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    first = await c.complete(model=S1, prompt="Do it once", idempotency_key="exec-9-r0")
    again = await c.complete(model=S1, prompt="Do it once", idempotency_key="exec-9-r0")
    assert again.text == first.text and len(fake_bridge.turns(S1)) == 1  # replayed by the bridge: one turn
    await c.complete(model=S1, prompt="Do it once")  # without the key the bridge has nothing to replay
    assert len(fake_bridge.turns(S1)) == 2
    await c.aclose()


async def test_prompt_limits_are_enforced_before_anything_is_sent(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge, max_prompt_chars=1000), KEY)
    too_long = await failure(c, model=S1, prompt="x" * 1001)
    assert too_long.code == "prompt_too_long" and not too_long.retryable and too_long.as_dict()["delivered"] is False
    empty = await failure(c, model=S1, prompt="   ")
    assert empty.code == "empty_prompt"
    assert fake_bridge.completions() == []
    await c.aclose()


# ---------------- every documented failure is classified ----------------
async def test_wrong_key(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge), "not-the-key-000000")
    err = await failure(c, model=S1, prompt="hello")
    assert (err.code, err.category, err.retryable, err.status) == ("invalid_key", "configuration", False, 401)
    assert err.as_dict()["delivered"] is False and "not-the-key" not in str(err)
    with pytest.raises(BridgeError) as e:
        await c.models()
    assert e.value.code == "invalid_key"
    await c.aclose()


@pytest.mark.parametrize("prompt,code,category,retryable,delivered", [
    ("[[fake:error:no_active_session:409]] x", "no_active_session", "session_unavailable", False, False),
    ("[[fake:error:bound_session_dead:409]] x", "bound_session_dead", "session_unavailable", False, False),
    ("[[fake:error:session_not_interactive:409]] x", "session_not_interactive", "session_unavailable", False, False),
    ("[[fake:error:credentials_unreadable:409]] x", "credentials_unreadable", "account_unavailable", False, False),
    ("[[fake:error:busy:409]] x", "busy", "upstream", True, False),
    ("[[fake:error]] x", "upstream_error", "upstream", True, None),
    ("[[fake:empty]] x", "empty_response", "invalid_response", False, None),
    ("[[fake:toolcall]] x", "unexpected_tool_call", "invalid_response", False, None),
    ("[[fake:garbage]] x", "invalid_response", "invalid_response", False, None),
])
@pytest.mark.parametrize("stream", [True, False])
async def test_failures_are_classified(fake_bridge, stream, prompt, code, category, retryable, delivered):
    c = ArenaBridgeClient(cfg(fake_bridge, stream=stream), KEY)
    err = await failure(c, model=S1, prompt=prompt)
    assert (err.code, err.category, err.retryable) == (code, category, retryable)
    # "not delivered" follows from the meaning of the bridge's code, whether it came as a status or as a stream frame;
    # a failure without such a code leaves the outcome unknown (None), never "nothing was sent"
    assert err.as_dict()["delivered"] is delivered
    assert err.request_id  # every failure can be matched with the bridge's own log
    await c.aclose()


async def test_session_owner_disabled_and_no_active_session(fake_bridge):
    fake_bridge.bridge.accounts[0]["disabled"] = True
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    err = await failure(c, model=S1, prompt="hello")
    assert (err.code, err.category, err.retryable, err.status) == (
        "session_account_unavailable", "account_unavailable", False, 409)
    assert ACCOUNTS[0] not in err.message and "o***@accounts.example.test" in err.message  # the owner is masked
    err = await failure(c, model="active", prompt="hello")
    assert (err.code, err.category) == ("no_active_session", "session_unavailable")
    assert fake_bridge.turns(S1) == []
    await c.aclose()


async def test_queue_full_and_rate_limit_carry_retry_after(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    fake_bridge.bridge.fail_next = [{"status": 503, "code": "bridge_queue_full", "message": "queue is full"}]
    err = await failure(c, model=S1, prompt="hello")
    assert (err.code, err.category, err.retryable, err.retry_after) == ("queue_full", "rate_limit", True, 10.0)
    assert err.as_dict()["delivered"] is False
    fake_bridge.bridge.rate_limit_rpm = 0
    err = await failure(c, model=S1, prompt="hello")
    assert (err.code, err.category, err.retryable, err.status) == ("rate_limited", "rate_limit", True, 429)
    assert err.retry_after and err.retry_after >= 1
    await c.aclose()


async def test_failure_after_the_stream_opened_is_reported_in_band(fake_bridge):
    c = ArenaBridgeClient(cfg(fake_bridge), KEY)
    err = await failure(c, model=S1, prompt="[[fake:midstream-error]] write a long answer")
    assert err.code == "upstream_error" and err.retryable and err.in_stream
    assert err.as_dict()["delivered"] is None and len(fake_bridge.turns(S1)) == 1  # the turn did reach the session
    await c.aclose()


async def test_a_silent_stream_is_stalled_and_a_long_turn_times_out(fake_bridge):
    fake_bridge.bridge.heartbeat_seconds = 30  # no heartbeats: the connection goes quiet
    c = ArenaBridgeClient(cfg(fake_bridge, idle_timeout=0.4), KEY)
    err = await failure(c, model=S1, prompt="[[fake:slow:3]] quiet")
    assert (err.code, err.category, err.retryable) == ("stalled", "bridge_unavailable", True)
    await c.aclose()
    fake_bridge.bridge.heartbeat_seconds = 0.1  # alive, but the turn is longer than the caller allows
    c = ArenaBridgeClient(cfg(fake_bridge, idle_timeout=5), KEY)
    err = await failure(c, model=session_id(2), prompt="[[fake:slow:3]] long", request_timeout=0.5)
    assert (err.code, err.retryable) == ("timeout", False)  # outcome unknown: never resent blindly
    assert err.as_dict()["delivered"] is None
    await c.aclose()


# ---------------- gateway adapter ----------------
def test_fold_prompt_keeps_rules_history_and_task_in_one_message():
    text = fold_prompt("RULE: answer in JSON", [
        {"role": "user", "content": "first question"}, {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "the actual task"}])
    assert text.index(RULES_HEADER) < text.index("RULE: answer in JSON") < text.index("first answer") \
        < text.index(TASK_HEADER) < text.index("the actual task")
    assert text.rstrip().endswith("the actual task")
    assert fold_prompt("", [{"role": "user", "content": "just this"}]) == f"{TASK_HEADER}\njust this"


async def test_adapter_folds_the_system_prompt_and_reports_usage_as_unknown(fake_bridge):
    a = ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings=TERMS)
    assert a.supports_tools is False and a.reports_usage is False and a.replay_window == 120.0
    req = ChatRequest(model=S1, system="PLATFORM RULE: never reveal secrets",
                      messages=[{"role": "user", "content": "Do the task"}], tools=["would-be-tool"],
                      metadata={"session_id": "acc-worker-1", "idempotency_key": "exec-1-r0"})
    evs = [e async for e in a.stream(req)]
    delivered = fake_bridge.turns(S1)[-1]  # the single user message that reached the "session"
    assert RULES_HEADER in delivered and "PLATFORM RULE: never reveal secrets" in delivered
    assert delivered.rstrip().endswith("Do the task")
    assert [e.type for e in evs][-1] == "usage" and evs[-1].usage == {"unknown": True}
    metas = [e.data for e in evs if e.type == "meta"]
    assert metas[0]["provider_request_id"] and metas[0]["mock"] is True and metas[0]["session"] == S1
    assert metas[-1]["final"] is True and metas[-1]["finish_reason"] == "stop"
    sent = fake_bridge.completions()[-1]
    assert sent["session"] == "acc-worker-1" and sent["idempotency"] == "exec-1-r0"


async def test_nothing_is_sent_before_the_terms_are_acknowledged(fake_bridge):
    a = ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings={})
    with pytest.raises(ProviderError, match="terms_not_acknowledged") as e:
        [ev async for ev in a.stream(ChatRequest(model=S1, messages=[{"role": "user", "content": "hello"}]))]
    assert e.value.details["delivered"] is False
    assert fake_bridge.completions() == [] and not fake_bridge.bridge.turns
    # the connection test uses the bridge's own probe model, which never reaches Arena, so it needs no acknowledgement
    probe = await ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings={}).test_connection()
    assert probe == {"ok": True, "models": 4, "version": "fake-1.1.0", "ready": True, "mock": True}
    assert not fake_bridge.bridge.turns


async def test_adapter_maps_failures_to_the_gateway_classes_with_details(fake_bridge):
    fake_bridge.bridge.fail_next = [{"status": 503, "code": "bridge_queue_full"}]
    a = ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings=TERMS)
    with pytest.raises(ProviderUnavailable) as e:
        [ev async for ev in a.stream(ChatRequest(model=S1, messages=[{"role": "user", "content": "hello"}]))]
    assert e.value.details["code"] == "queue_full" and e.value.retry_after == 10.0
    fake_bridge.bridge.accounts[0]["disabled"] = True
    a = ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings=TERMS)
    with pytest.raises(ProviderError) as e2:
        [ev async for ev in a.stream(ChatRequest(model=S1, messages=[{"role": "user", "content": "hello"}]))]
    assert e2.value.details["category"] == "account_unavailable" and e2.value.status == 409


async def test_adapter_lists_sessions_without_the_probe_model(fake_bridge):
    found = await ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings={}).list_models()
    assert [m.name for m in found] == [session_id(i) for i in range(1, 5)]
    assert found[0].display_name == "fake-model-1"


async def _ask(adapter: ArenaLocalBridgeAdapter, model: str, prompt: str) -> list:
    return [e async for e in adapter.stream(ChatRequest(model=model, messages=[{"role": "user", "content": prompt}]))]


async def test_requests_wait_on_the_platform_side_for_a_free_bridge_slot(fake_bridge):
    def one(n: int, **settings) -> ArenaLocalBridgeAdapter:
        return ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings={**TERMS, **settings})

    # default: one request in flight per bridge — the second waits here, where nothing has been sent yet
    runs = await asyncio.gather(*[_ask(one(i), session_id(i), f"[[fake:slow:0.3]] task {i}") for i in (1, 2, 3)])
    assert fake_bridge.bridge.peak_depth == 1 and len(fake_bridge.completions()) == 3
    waits = [e.data["waited_for_bridge_slot_ms"] for evs in runs for e in evs
             if e.type == "meta" and "waited_for_bridge_slot_ms" in e.data]
    assert len(waits) == 2 and max(waits) >= 300  # recorded, so a slow task can be told from a waiting one
    assert all(evs[-1].type == "usage" for evs in runs)
    # raising the limit lets the bridge's own queue order them instead
    fake_bridge.bridge.peak_depth = 0
    await asyncio.gather(*[_ask(one(i, max_concurrent=3), session_id(i), f"[[fake:slow:0.2]] again {i}")
                           for i in (1, 2, 3)])
    assert fake_bridge.bridge.peak_depth == 3


async def test_a_failed_or_cancelled_request_frees_its_slot(fake_bridge):
    a = ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings=TERMS)
    with pytest.raises(ProviderError):
        await _ask(a, S1, "[[fake:error:no_active_session:409]] x")
    hanging = asyncio.create_task(_ask(ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings=TERMS),
                                       S1, "[[fake:slow:30]] never finishes in time"))
    await asyncio.sleep(0.3)
    hanging.cancel()
    with pytest.raises(asyncio.CancelledError):
        await hanging
    evs = await asyncio.wait_for(_ask(ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings=TERMS),
                                      session_id(2), "after the cancelled one"), 10)
    assert evs[-1].type == "usage"
    # the probe model is answered by the bridge process itself and never waits for a slot
    probe = await _ask(ArenaLocalBridgeAdapter(KEY, base_url=fake_bridge.url, settings={}), "ping", "ping")
    assert "".join(e.text for e in probe if e.type == "delta") == "pong"


def test_adapter_requires_a_valid_address():
    with pytest.raises(ProviderError):
        ArenaLocalBridgeAdapter(KEY, base_url=None)
    with pytest.raises(ProviderError, match="invalid bridge address"):
        ArenaLocalBridgeAdapter(KEY, base_url="http://user:pw@127.0.0.1:1")


# ---------------- API engine (executions) ----------------
@pytest.fixture()
def bridge_provider(admin_client, fake_bridge):
    r = admin_client.post("/api/workforce/bridges", json={
        "name": f"Bridge {uuid.uuid4().hex[:8]}", "base_url": fake_bridge.url, "api_key": KEY})
    assert r.status_code == 201, r.text
    b = r.json()
    r = admin_client.post(f"/api/workforce/bridges/{b['id']}/terms", json={"statement": b["terms"]["statement"]})
    assert r.status_code == 200, r.text
    return r.json()


def _agent(db, provider_id: int, model: str = S1, attempts: int = 1, **kw) -> Agent:
    name = f"bridge-worker-{uuid.uuid4().hex[:8]}"
    a = Agent(organization_id=1, name=name, identifier=name, role="research", state="IDLE", execution_engine="api",
              system_instructions="Answer briefly.", provider_id=provider_id, model=model,
              retry_policy={"max_attempts": attempts, "backoff_seconds": 0, "backoff_multiplier": 1}, **kw)
    db.add(a)
    db.commit()
    return a


def _run(admin_client, agent: Agent, prompt: str) -> dict:
    r = admin_client.post("/api/executions", json={"agent_id": agent.id, "input": {"prompt": prompt}})
    assert r.status_code == 201, r.text
    return admin_client.get(f"/api/executions/{r.json()['id']}").json()


def _events(db, execution_id: str, type_: str) -> list[dict]:
    return [e.payload for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == execution_id, ExecutionEvent.type == type_).order_by(ExecutionEvent.seq))]


def test_execution_through_the_bridge_is_labelled_and_usage_is_not_reported(admin_client, fake_bridge,
                                                                            bridge_provider, db):
    agent = _agent(db, bridge_provider["id"], tool_permissions=["repo.read"])
    ex = _run(admin_client, agent, "Say hi")
    assert ex["status"] == "COMPLETED" and ex["provider"] == "arena_local_bridge" and ex["model"] == S1
    assert ex["output"]["text"].startswith("[FAKE ARENA BRIDGE — not Arena]")
    assert ex["is_mock"] is True  # the fake identified itself: the run can never pass for an Arena result
    assert ex["input_tokens"] is None and ex["output_tokens"] is None and ex["cost_usd"] is None  # not reported ≠ 0
    assert ex["provider_request_id"]
    usage = db.scalar(select(UsageRecord).where(UsageRecord.execution_id == ex["id"]))
    assert usage.usage_known is False and usage.is_mock and usage.provider_request_id == ex["provider_request_id"]
    assert db.scalar(select(CostRecord).where(CostRecord.usage_record_id == usage.id)) is None
    assert db.get(Provider, bridge_provider["id"]).is_mock is True
    sent = fake_bridge.completions()[-1]
    assert sent["session"] == f"acc-agent-{agent.identifier}" and sent["idempotency"] == f"acc-{ex['id']}-r0"
    delivered = fake_bridge.turns(S1)[-1]
    assert RULES_HEADER in delivered and "Answer briefly." in delivered and "Say hi" in delivered
    assert any("offers no platform tools" in p.get("message", "") for p in _events(db, ex["id"], "log"))
    assert _events(db, ex["id"], "provider")[0]["provider_request_id"] == ex["provider_request_id"]


def test_execution_is_refused_until_the_terms_are_acknowledged(admin_client, fake_bridge, db):
    r = admin_client.post("/api/workforce/bridges", json={
        "name": f"Unacknowledged {uuid.uuid4().hex[:8]}", "base_url": fake_bridge.url, "api_key": KEY})
    agent = _agent(db, r.json()["id"])
    ex = _run(admin_client, agent, "Say hi")
    assert ex["status"] == "FAILED" and ex["error_category"] == "provider" and "terms_not_acknowledged" in ex["error"]
    assert fake_bridge.completions() == []  # not one request left the platform
    assert ex["input_tokens"] is None  # nothing was measured


def test_retry_resends_with_the_same_idempotency_key_inside_the_replay_window(admin_client, fake_bridge,
                                                                              bridge_provider, db):
    fake_bridge.bridge.fail_next = [{"status": 502, "message": "FAKE transient failure"}]
    agent = _agent(db, bridge_provider["id"], attempts=2)
    ex = _run(admin_client, agent, "Do it once")
    assert ex["status"] == "COMPLETED" and ex["attempt"] == 2
    keys = [c["idempotency"] for c in fake_bridge.completions()]
    assert keys == [f"acc-{ex['id']}-r0"] * 2  # the retry names the same request
    assert len(fake_bridge.turns(S1)) == 1
    assert any("resending the same request" in p.get("message", "") for p in _events(db, ex["id"], "log"))
    assert _events(db, ex["id"], "provider_error")[0]["code"] == "upstream_error"


def test_no_blind_resend_after_the_replay_window(admin_client, fake_bridge, bridge_provider, db):
    r = admin_client.patch(f"/api/workforce/bridges/{bridge_provider['id']}", json={"settings": {"replay_window_s": 0}})
    assert r.status_code == 200 and r.json()["settings"]["replay_window_s"] == 0
    agent = _agent(db, bridge_provider["id"], attempts=3)
    ex = _run(admin_client, agent, "[[fake:midstream-error]] write the report")
    assert ex["status"] == "FAILED" and ex["error_category"] == "provider" and "outcome_unknown" in ex["error"]
    assert len(fake_bridge.completions()) == 1 and len(fake_bridge.turns(S1)) == 1  # delivered once, never resent
    codes = [p["code"] for p in _events(db, ex["id"], "provider_error")]
    assert codes == ["upstream_error", "outcome_unknown"]
    assert ex["output"]["text"] == ""  # the half answer of the failed attempt is in the events, never the result
    assert "".join(p["text"] for p in _events(db, ex["id"], "output_delta"))  # …where it stays as evidence


def test_a_request_the_bridge_refused_is_not_an_open_outcome(admin_client, fake_bridge, bridge_provider, db):
    admin_client.patch(f"/api/workforce/bridges/{bridge_provider['id']}", json={"settings": {"replay_window_s": 0}})
    fake_bridge.bridge.fail_next = [{"status": 503, "code": "bridge_queue_full", "retry_after": 1}]
    agent = _agent(db, bridge_provider["id"], attempts=2)
    ex = _run(admin_client, agent, "Queue me")
    assert ex["status"] == "COMPLETED" and ex["attempt"] == 2  # refused before delivery: sending again is safe
    retry = _events(db, ex["id"], "retry")[0]
    assert retry["after_seconds"] == 1.0  # the bridge's Retry-After is honoured
    assert len(fake_bridge.turns(S1)) == 1


def test_account_failure_is_not_retried_and_names_its_category(admin_client, fake_bridge, bridge_provider, db):
    fake_bridge.bridge.accounts[0]["disabled"] = True
    agent = _agent(db, bridge_provider["id"], attempts=3)
    ex = _run(admin_client, agent, "hello")
    assert ex["status"] == "FAILED" and ex["attempt"] == 1 and ex["error_category"] == "provider"
    err = _events(db, ex["id"], "provider_error")[0]
    assert err["code"] == "session_account_unavailable" and err["category"] == "account_unavailable"
    assert err["delivered"] is False and ACCOUNTS[0] not in str(err) and ACCOUNTS[0] not in ex["error"]
    assert db.get(Execution, ex["id"]).attempt == 1


def test_the_bridge_adapter_is_registered_for_real():
    from app.connectivity.gateway.base import ADAPTERS
    assert ADAPTERS["arena_local_bridge"] is ArenaLocalBridgeAdapter and ArenaLocalBridgeAdapter.is_mock is False
    assert asyncio.iscoroutinefunction(ArenaLocalBridgeAdapter.test_connection)
