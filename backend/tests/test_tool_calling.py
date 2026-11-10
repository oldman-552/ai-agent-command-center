"""Model tool calling (data plane: tool invocation). Each adapter is exercised against its provider's real streaming
format simulated at the HTTP layer; the engine loop runs with the labelled mock provider and real platform tools."""
from __future__ import annotations

import asyncio
import json
import uuid

import httpx2 as httpx
import pytest
from sqlalchemy import select

import app.main  # noqa: F401  (registers engines and tools)
from app.connectivity.gateway.adapters import AnthropicAdapter, GoogleAdapter, MockAdapter, OpenAIAdapter
from app.connectivity.gateway.base import ChatRequest
from app.connectivity.gateway.toolspec import ToolSpec, model_name
from app.models import Agent, Checkpoint, Execution, ExecutionEvent, Memory, Project, Provider
from app.runtime import service as rt

SPEC = ToolSpec("repo_read", "repo.read", "Read files", {"type": "object", "additionalProperties": False,
                                                       "properties": {"path": {"type": "string"}}, "required": ["path"]})
HISTORY = [{"role": "user", "content": "list files"},
           {"role": "assistant", "content": "Looking.", "tool_calls": [
               {"id": "t1", "name": "repo_read", "arguments": {"path": "."}}]},
           {"role": "tool", "tool_call_id": "t1", "name": "repo_read", "content": "{\"files\": [\"a.py\"]}"}]


async def collect(adapter, req):
    return [e async for e in adapter.stream(req)]


def sse(events):
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def test_anthropic_tool_use_and_history_conversion():
    seen = {}
    stream = sse([
        ("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant",
         "model": "c", "content": [], "stop_reason": None, "stop_sequence": None,
         "usage": {"input_tokens": 5, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "tool_use", "id": "toolu_1", "name": "repo_read", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta", "partial_json": "{\"path\": \"src"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta", "partial_json": "/app.py\"}"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                           "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"})])

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})
    a = AnthropicAdapter("sk-ant-test-0000000000", http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    evs = asyncio.run(collect(a, ChatRequest(model="c", messages=HISTORY, tools=[SPEC])))
    calls = [e.call for e in evs if e.type == "tool_call"]
    assert calls == [{"id": "toolu_1", "name": "repo_read", "arguments": {"path": "src/app.py"}}]
    assert seen["tools"] == [{"name": "repo_read", "description": "Read files", "input_schema": SPEC.parameters}]
    assert seen["messages"][1]["content"] == [{"type": "text", "text": "Looking."},
                                              {"type": "tool_use", "id": "t1", "name": "repo_read", "input": {"path": "."}}]
    assert seen["messages"][2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "{\"files\": [\"a.py\"]}"}]}


def test_openai_tool_call_fragments_and_history_conversion():
    seen = {}
    base = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "g"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "type": "function", "function": {"name": "repo_read", "arguments": ""}}]},
            "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":"}}]}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": " \"README.md\"}"}}]}, "finish_reason": "tool_calls"}]},
        {**base, "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}}]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    a = OpenAIAdapter("sk-test-1234567890", http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    evs = asyncio.run(collect(a, ChatRequest(model="g", messages=HISTORY, tools=[SPEC])))
    assert [e.call for e in evs if e.type == "tool_call"] == [
        {"id": "call_a", "name": "repo_read", "arguments": {"path": "README.md"}}]
    assert seen["tools"][0] == {"type": "function", "function": {"name": "repo_read", "description": "Read files",
                                                                 "parameters": SPEC.parameters}}
    assert seen["messages"][1]["tool_calls"][0]["function"] == {"name": "repo_read", "arguments": "{\"path\": \".\"}"}
    assert seen["messages"][2] == {"role": "tool", "tool_call_id": "t1", "content": "{\"files\": [\"a.py\"]}"}


def test_gemini_function_call_schema_cleanup_and_history_conversion():
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        lines = [{"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "repo_read", "args": {"path": "docs"}}}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1}}]
        return httpx.Response(200, content="".join(f"data: {json.dumps(x)}\r\n\r\n" for x in lines).encode())
    a = GoogleAdapter("g-key-123456", http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    evs = asyncio.run(collect(a, ChatRequest(model="gm", messages=HISTORY, tools=[SPEC])))
    calls = [e.call for e in evs if e.type == "tool_call"]
    assert calls[0]["name"] == "repo_read" and calls[0]["arguments"] == {"path": "docs"} and calls[0]["id"]
    decl = seen["tools"][0]["functionDeclarations"][0]
    assert "additionalProperties" not in decl["parameters"] and decl["parameters"]["required"] == ["path"]
    assert seen["contents"][1] == {"role": "model", "parts": [{"text": "Looking."},
                                                              {"functionCall": {"name": "repo_read", "args": {"path": "."}}}]}
    assert seen["contents"][2]["parts"][0]["functionResponse"] == {"name": "repo_read",
                                                                   "response": {"result": "{\"files\": [\"a.py\"]}"}}


def test_model_names_are_valid_and_unique_for_every_provider():
    taken: set[str] = set()
    names = [model_name(k, taken) for k in ("repo.read", "mcp:my server:read.file", "repo_read", "x" * 90)]
    assert all(__import__("re").fullmatch(r"[A-Za-z0-9_-]{1,64}", n) for n in names) and len(set(names)) == 4


def test_mock_adapter_requests_offered_tools_only():
    evs = asyncio.run(collect(MockAdapter(), ChatRequest(model="m", tools=[SPEC], messages=[
        {"role": "user", "content": 'do it [[mock:call repo.read {"path": "x"}]] [[mock:call shell.test {}]]'}])))
    assert [e.call["name"] for e in evs if e.type == "tool_call"] == ["repo_read"]
    assert "shell.test is not offered" in "".join(e.text for e in evs if e.type == "delta")


# ---- the engine loop with real platform tools ----
@pytest.fixture
def mock_provider(db):
    return db.scalar(select(Provider).where(Provider.kind == "mock", Provider.organization_id == 1))


def _agent(db, provider, **kw):
    a = Agent(organization_id=1, name=f"Tools {uuid.uuid4().hex[:6]}", identifier=f"tools-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", provider_id=provider.id, model="mock-model",
              security_profile="developer", timeout_seconds=60, concurrency_limit=5,
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1}, **kw)
    db.add(a)
    db.commit()
    return a


def _run(db, agent, prompt, project):
    ex = rt.create_execution(db, organization_id=1, agent=agent, input={"prompt": prompt}, actor="test",
                             project_id=project.id)
    db.commit()
    status = asyncio.run(rt.run_execution(ex.id))
    db.expire_all()
    return status, db.get(Execution, ex.id)


def _events(db, ex_id, type_):
    return db.scalars(select(ExecutionEvent).where(ExecutionEvent.execution_id == ex_id, ExecutionEvent.type == type_)
                      .order_by(ExecutionEvent.seq)).all()


def test_engine_runs_a_permitted_tool_and_returns_its_result_to_the_model(db, mock_provider):
    p = Project(organization_id=1, name=f"TC {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    a = _agent(db, mock_provider, tool_permissions=["memory.write"])
    status, ex = _run(db, a, 'remember [[mock:call memory.write {"type": "lesson", "title": "Cache", '
                             '"content": "Clear the cache after deploys."}]]', p)
    assert status == "COMPLETED", ex.error
    calls = _events(db, ex.id, "tool_call")
    assert [c.payload["tool"] for c in calls] == ["memory.write"]  # through the runtime: permission-checked, audited
    m = db.scalars(select(Memory).where(Memory.project_id == p.id)).one()
    assert m.verification == "suggestion" and m.content == "Clear the cache after deploys."
    assert "tool results: memory_write ->" in ex.output["text"] and f'"memory_id": {m.id}' in ex.output["text"]
    cp = db.scalars(select(Checkpoint).where(Checkpoint.execution_id == ex.id).order_by(Checkpoint.seq)).all()[-1]
    assert [x["role"] for x in cp.state["messages"]] == ["user", "assistant", "tool"] and cp.state["tool_rounds"] == 1


def test_tools_outside_the_permissions_are_not_offered_or_run(db, mock_provider):
    p = Project(organization_id=1, name=f"TC {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    a = _agent(db, mock_provider, tool_permissions=["repo.read"])
    status, ex = _run(db, a, 'write [[mock:call memory.write {"type": "lesson", "title": "x", "content": "y"}]]', p)
    assert status == "COMPLETED" and "memory.write is not offered" in ex.output["text"]
    assert _events(db, ex.id, "tool_call") == [] and db.scalars(select(Memory).where(Memory.project_id == p.id)).all() == []


def test_tool_round_limit_stops_a_looping_model(db, mock_provider):
    p = Project(organization_id=1, name=f"TC {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    a = _agent(db, mock_provider, tool_permissions=["memory.write"], context_policy={"max_tool_rounds": 0})
    status, ex = _run(db, a, 'x [[mock:call memory.write {"type": "lesson", "title": "t", "content": "c"}]]', p)
    assert status == "FAILED" and "max_tool_rounds" in (ex.error or "")


def test_a_resumed_execution_continues_the_conversation_without_rerunning_tools(db, mock_provider):
    p = Project(organization_id=1, name=f"TC {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    a = _agent(db, mock_provider, tool_permissions=["memory.write"])
    ex = rt.create_execution(db, organization_id=1, agent=a, input={"prompt": "remember [[mock:call memory.write {}]]"},
                             actor="test", project_id=p.id)
    db.flush()
    msgs = [{"role": "user", "content": "remember"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c0", "name": "memory_write", "arguments": {}}]},
            {"role": "tool", "tool_call_id": "c0", "name": "memory_write", "content": "{\"memory_id\": 42}"}]
    db.add(Checkpoint(execution_id=ex.id, seq=1, verified=True,
                      state={"messages": msgs, "tool_rounds": 1, "route": [mock_provider.id, "mock-model"], "parts": []}))
    ex.latest_checkpoint_seq = 1
    for to in ("INITIALIZING", "READY", "RUNNING", "TERMINATED"):
        rt.set_status(db, ex, rt.X(to))
    db.commit()
    assert asyncio.run(rt.run_execution(ex.id)) == "COMPLETED"
    db.expire_all()
    done = db.get(Execution, ex.id)
    assert "memory_write -> {\"memory_id\": 42}" in done.output["text"]
    assert _events(db, ex.id, "tool_call") == []  # the tool from before the crash is not run again


# ---- WAITING_FOR_INPUT: the agent asks a person ----
ASK = '[[mock:call ask.human {"question": "Which database should I use?"}]]'


def test_agent_question_parks_the_execution_until_a_person_answers(db, mock_provider, admin_client):
    p = Project(organization_id=1, name=f"TC {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    a = _agent(db, mock_provider, tool_permissions=["ask.human"])
    status, ex = _run(db, a, f"set up storage {ASK}", p)
    assert status == "WAITING_FOR_INPUT"
    detail = admin_client.get(f"/api/executions/{ex.id}").json()
    assert detail["status"] == "WAITING_FOR_INPUT" and detail["pending_question"] == "Which database should I use?"
    assert [e.payload["question"] for e in _events(db, ex.id, "input_required")] == ["Which database should I use?"]
    db.refresh(a)
    assert a.state == "WAITING_FOR_INPUT"
    r = admin_client.post(f"/api/executions/{ex.id}/input", json={"answer": "PostgreSQL 16"})
    assert r.status_code == 200, r.text
    db.expire_all()
    done = db.get(Execution, ex.id)
    assert done.status == "COMPLETED", done.error
    assert 'ask_human -> {"answer": "PostgreSQL 16"' in done.output["text"]
    assert admin_client.get(f"/api/executions/{ex.id}").json()["pending_question"] is None
    assert admin_client.post(f"/api/executions/{ex.id}/input", json={"answer": "again"}).status_code == 409
    assert [e.type for e in _events(db, ex.id, "input_answered")] == ["input_answered"]


def test_a_waiting_execution_can_be_cancelled_and_asking_needs_the_permission(db, mock_provider, admin_client):
    p = Project(organization_id=1, name=f"TC {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    a = _agent(db, mock_provider, tool_permissions=["ask.human"])
    _, ex = _run(db, a, f"x {ASK}", p)
    assert admin_client.post(f"/api/executions/{ex.id}/cancel").json()["status"] == "CANCELLED"
    b = _agent(db, mock_provider, tool_permissions=[])  # not permitted: not offered, never parks
    status, ex2 = _run(db, b, f"x {ASK}", p)
    assert status == "COMPLETED" and _events(db, ex2.id, "input_required") == []  # no tools offered: just answers


def test_a2a_input_required_and_reply_with_task_id(client, admin_client, db, mock_provider):
    from tests.test_a2a import V, rpc
    a = _agent(db, mock_provider, tool_permissions=["ask.human"])
    a.security_profile = "developer"
    db.commit()
    token = admin_client.post("/api/a2a/keys", json={"name": "ask", "agent_ids": [a.id]}).json()["token"]
    m = {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": f"plan it {ASK}"}]}}
    t = rpc(client, a, token, "SendMessage", m)["result"]["task"]
    assert t["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert t["status"]["message"]["parts"][0]["text"] == "Which database should I use?"
    reply = {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "taskId": t["id"],
                         "parts": [{"text": "SQLite is fine"}]}}
    t2 = rpc(client, a, token, "SendMessage", reply)["result"]["task"]
    assert t2["id"] == t["id"] and t2["status"]["state"] == "TASK_STATE_COMPLETED"  # the same task continued
    assert '"answer": "SQLite is fine"' in t2["artifacts"][0]["parts"][0]["text"]
    assert V
