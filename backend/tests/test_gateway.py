"""M4 — GW-01..GW-06 + EE-01. External APIs are simulated at the HTTP layer (httpx.MockTransport) with the
providers' real streaming formats, so the official SDKs are exercised end to end."""
import asyncio
import json
import uuid
from decimal import Decimal

import httpx2 as httpx
import pytest
from sqlalchemy import select

from app.connectivity.gateway import service as gw
from app.connectivity.gateway.adapters import AnthropicAdapter, GoogleAdapter, OpenAIAdapter
from app.connectivity.gateway.base import ChatRequest, ProviderError, ProviderUnavailable
from app.models import Agent, ApiCredential, AuditLog, CostRecord, UsageRecord
from app.runtime import service as rt

KEY = "sk-ant-api03-TESTKEY-abcdefghijklmnop-WXYZ"


def sse(events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


ANTHROPIC_STREAM = sse([
    ("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
     "model": "claude-test", "content": [], "stop_reason": None, "stop_sequence": None,
     "usage": {"input_tokens": 1000, "output_tokens": 1, "cache_read_input_tokens": 200}}}),
    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}),
    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " world"}}),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                       "usage": {"output_tokens": 500}}),
    ("message_stop", {"type": "message_stop"}),
])


def anthropic_handler(status_code=200):
    def h(req: httpx.Request):
        # SDK v2 sends "Authorization: Bearer <key>"; older versions used x-api-key
        sent_key = req.headers.get("x-api-key") or req.headers.get("authorization", "").removeprefix("Bearer ")
        assert sent_key == KEY
        if req.url.path.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "claude-test", "type": "model", "display_name": "Claude Test",
                                                       "created_at": "2026-01-01T00:00:00Z"}],
                                             "has_more": False, "first_id": "claude-test", "last_id": "claude-test"})
        if status_code != 200:
            return httpx.Response(status_code, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
        body = json.loads(req.content)
        assert body["system"] and body["messages"][0]["content"] == "Say hi"
        return httpx.Response(200, content=ANTHROPIC_STREAM, headers={"content-type": "text/event-stream"})
    return h


async def collect(adapter, req):
    return [e async for e in adapter.stream(req)]


def test_anthropic_adapter_streams_text_and_usage():
    a = AnthropicAdapter(KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler())))
    evs = asyncio.run(collect(a, ChatRequest(model="claude-test", messages=[{"role": "user", "content": "Say hi"}], system="sys")))
    assert "".join(e.text for e in evs if e.type == "delta") == "Hello world"
    assert evs[-1].usage == {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 200}


def test_openai_adapter_streams_text_and_usage():
    chunks = [{"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test",
               "choices": [{"index": 0, "delta": {"content": t}, "finish_reason": None}]} for t in ("Hi", " there")]
    chunks.append({"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test", "choices": [],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13,
                             "prompt_tokens_details": {"cached_tokens": 4}}})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def h(req):
        sent = json.loads(req.content)
        assert sent["messages"][0] == {"role": "system", "content": "sys"} and sent["stream_options"]["include_usage"]
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    a = OpenAIAdapter("sk-test-1234567890", http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    evs = asyncio.run(collect(a, ChatRequest(model="gpt-test", messages=[{"role": "user", "content": "x"}], system="sys")))
    assert "".join(e.text for e in evs if e.type == "delta") == "Hi there"
    assert evs[-1].usage == {"input_tokens": 10, "output_tokens": 3, "cached_tokens": 4}


def test_google_adapter_streams_text_usage_and_lists_models():
    def h(req):
        assert req.headers["x-goog-api-key"] == "g-key-123456"
        if req.method == "GET":
            return httpx.Response(200, json={"models": [
                {"name": "models/gemini-test", "displayName": "Gemini Test", "inputTokenLimit": 1000000,
                 "outputTokenLimit": 8192, "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]})
        sent = json.loads(req.content)
        assert sent["systemInstruction"]["parts"][0]["text"] == "sys" and sent["contents"][0]["role"] == "user"
        lines = [{"candidates": [{"content": {"parts": [{"text": "Mar"}]}}]},
                 {"candidates": [{"content": {"parts": [{"text": "haba"}]}}],
                  "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}}]
        return httpx.Response(200, content="".join(f"data: {json.dumps(x)}\r\n\r\n" for x in lines).encode())
    a = GoogleAdapter("g-key-123456", http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    evs = asyncio.run(collect(a, ChatRequest(model="gemini-test", messages=[{"role": "user", "content": "x"}], system="sys")))
    assert "".join(e.text for e in evs if e.type == "delta") == "Marhaba"
    assert evs[-1].usage == {"input_tokens": 7, "output_tokens": 2, "cached_tokens": 0}
    models = asyncio.run(a.list_models())
    assert [m.name for m in models] == ["gemini-test"] and models[0].context_window == 1000000


@pytest.mark.parametrize("code,exc", [(429, ProviderUnavailable), (529, ProviderUnavailable), (503, ProviderUnavailable),
                                      (401, ProviderError), (400, ProviderError)])
def test_error_classification(code, exc):
    a = AnthropicAdapter(KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler(code))))
    with pytest.raises(exc):
        asyncio.run(collect(a, ChatRequest(model="m", messages=[{"role": "user", "content": "Say hi"}], system="s")))


def test_missing_key_explains_subscription_is_not_api():
    with pytest.raises(ProviderError, match="subscription"):
        AnthropicAdapter(None)


@pytest.fixture()
def anthropic_provider(admin_client):
    gw.HTTP_CLIENTS["anthropic"] = httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler()))
    r = admin_client.post("/api/providers", json={"name": f"Anthropic {uuid.uuid4().hex[:8]}", "kind": "anthropic", "api_key": KEY})
    assert r.status_code == 201, r.text
    yield r.json()
    gw.HTTP_CLIENTS.pop("anthropic", None)


def test_credentials_encrypted_write_only_and_never_logged(admin_client, anthropic_provider, db):
    p = anthropic_provider
    assert p["credential_hint"] == "…WXYZ" and p["has_credential"] and KEY not in json.dumps(p)
    assert KEY not in json.dumps(admin_client.get("/api/providers").json())
    cred = db.scalar(select(ApiCredential).where(ApiCredential.name.like(f"{p['name']}%")))
    assert KEY not in cred.encrypted_value
    assert not any(KEY in json.dumps(a.details) + a.message for a in db.scalars(select(AuditLog)))


def test_connection_test_and_model_discovery_never_invent_prices(admin_client, anthropic_provider):
    pid = anthropic_provider["id"]
    assert admin_client.post(f"/api/providers/{pid}/test").json()["status"] == "ok"
    assert admin_client.post(f"/api/providers/{pid}/discover-models").json() == {"discovered": 1, "added": 1}
    m = admin_client.get("/api/providers").json()
    model = next(p for p in m if p["id"] == pid)["models"][0]
    assert model["name"] == "claude-test" and model["input_price_per_mtok"] is None and model["discovered"]


def _agent(db, name, **kw):
    a = Agent(organization_id=1, name=name, identifier=f"gw-{name}", role="backend", state="IDLE",
              execution_engine="api", system_instructions="Be precise.",
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1}, **kw)
    db.add(a)
    db.commit()
    return a


def test_api_engine_end_to_end_with_priced_model(admin_client, anthropic_provider, db):
    pid = anthropic_provider["id"]
    mid = admin_client.post(f"/api/providers/{pid}/models", json={
        "name": "claude-test", "input_price_per_mtok": "3", "output_price_per_mtok": "15",
        "cached_input_price_per_mtok": "0.30"}).json()["id"]
    assert mid
    a = _agent(db, "priced", provider_id=pid, model="claude-test")
    r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"prompt": "Say hi"}})
    ex = admin_client.get(f"/api/executions/{r.json()['id']}").json()
    assert ex["status"] == "COMPLETED" and ex["output"]["text"] == "Hello world" and ex["is_mock"] is False
    assert ex["provider"] == "anthropic" and ex["input_tokens"] == 1000 and ex["output_tokens"] == 500
    # (800 uncached * 3 + 200 cached * 0.30 + 500 * 15) / 1e6 = 0.00996
    assert Decimal(ex["cost_usd"]) == Decimal("0.009960")
    usage = db.scalar(select(UsageRecord).where(UsageRecord.execution_id == ex["id"]))
    assert usage.cached_tokens == 200 and not usage.is_mock
    assert db.scalar(select(CostRecord).where(CostRecord.usage_record_id == usage.id)).amount_usd == Decimal("0.009960")


def test_unknown_price_means_no_cost_record(admin_client, anthropic_provider, db):
    a = _agent(db, "unpriced", provider_id=anthropic_provider["id"], model="some-unlisted-model")
    r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"prompt": "Say hi"}})
    ex = admin_client.get(f"/api/executions/{r.json()['id']}").json()
    assert ex["status"] == "COMPLETED" and ex["cost_usd"] is None
    usage = db.scalar(select(UsageRecord).where(UsageRecord.execution_id == ex["id"]))
    assert db.scalar(select(CostRecord).where(CostRecord.usage_record_id == usage.id)) is None


def test_fallback_on_primary_failure_is_used_and_audited(admin_client, db):
    gw.HTTP_CLIENTS["anthropic"] = httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler(529)))
    try:
        p = admin_client.post("/api/providers", json={"name": "Overloaded", "kind": "anthropic", "api_key": KEY}).json()
        mock_id = next(x["id"] for x in admin_client.get("/api/providers").json() if x["kind"] == "mock")
        a = _agent(db, "fallback", provider_id=p["id"], model="claude-test", fallback_provider_id=mock_id,
                   fallback_model="mock-model")
        r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"prompt": "Say hi"}})
        ex = admin_client.get(f"/api/executions/{r.json()['id']}").json()
        assert ex["status"] == "COMPLETED" and ex["provider"] == "mock" and ex["is_mock"] is True
        assert ex["output"]["text"].startswith("[MOCK — not real AI]")
        assert db.scalar(select(AuditLog).where(AuditLog.action == "gateway.fallback", AuditLog.execution_id == ex["id"]))
    finally:
        gw.HTTP_CLIENTS.pop("anthropic", None)


def test_no_provider_configured_fails_cleanly(admin_client, db):
    a = _agent(db, "noprov")
    r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"prompt": "x"}})
    ex = admin_client.get(f"/api/executions/{r.json()['id']}").json()
    assert ex["status"] == "FAILED" and "no enabled provider" in ex["error"]


def test_api_engine_is_registered_for_real():
    from app.runtime.contract import registered_engines
    assert "api" in registered_engines() and isinstance(rt, object)


def test_anthropic_adapter_does_not_send_a_parameter_the_sdk_no_longer_takes():
    # Regression (owner's machine): the Manager asks with temperature=0; anthropic 2.x removed sampling parameters
    # from Messages.stream, so the call raised TypeError and no objective could be planned. The adapter must only
    # pass what the installed SDK accepts — and the request must still go out.
    from app.connectivity.gateway import adapters
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=ANTHROPIC_STREAM, headers={"content-type": "text/event-stream"})
    a = AnthropicAdapter(KEY, http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    evs = asyncio.run(collect(a, ChatRequest(model="claude-test", messages=[{"role": "user", "content": "plan"}],
                                             system="s", temperature=0, max_tokens=100)))
    assert "".join(e.text for e in evs if e.type == "delta") == "Hello world"
    assert seen["model"] == "claude-test"
    assert ("temperature" in seen) == adapters._anthropic_accepts("temperature")
