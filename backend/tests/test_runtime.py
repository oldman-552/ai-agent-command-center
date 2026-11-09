"""M2 — RT-01..RT-08: contract, state machine, events, streaming, cancel/timeout, checkpoints, tool gate, retries."""
import asyncio

import pytest
from sqlalchemy import select

import app.main  # noqa: F401  — registers real engines first, so the mock rebinding below sticks
from app.models import Agent, AuditLog, Checkpoint, Execution, ExecutionEvent
from app.runtime import contract, service
from app.runtime.engines.mock import MockEngine


@pytest.fixture(autouse=True)
def mock_api_engine(monkeypatch):
    """Bind the TEST-ONLY mock engine to the 'api' engine kind for these tests (real one arrives with M4)."""
    monkeypatch.setitem(contract._ENGINES, "api", MockEngine)


def _agent(db, name, **kw):
    a = Agent(organization_id=1, name=name, identifier=f"rt-{name}", role="backend", state="IDLE",
              execution_engine="api", tool_permissions=kw.pop("tools", ["repo.read"]),
              retry_policy=kw.pop("retry", {"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1}),
              timeout_seconds=kw.pop("timeout", 30), **kw)
    db.add(a)
    db.commit()
    return a


def _exec(db, agent, script, **kw):
    ex = service.create_execution(db, organization_id=1, agent=agent, input={"mock_script": script}, actor="test", **kw)
    db.commit()
    return ex


def _states(db, ex_id):
    return [e.payload["to"] for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == ex_id, ExecutionEvent.type == "state").order_by(ExecutionEvent.seq))]


def run(ex_id):
    return asyncio.run(service.run_execution(ex_id))


def test_contract_is_provider_independent():
    import ast
    import pathlib
    for f in ("contract.py", "service.py"):
        tree = ast.parse(pathlib.Path(f"app/runtime/{f}").read_text())
        mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
               {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.split(".")[0] in {"anthropic", "openai", "google", "langgraph", "litellm"} for m in mods), f


def test_successful_run_records_full_contract(db):
    a = _agent(db, "ok")
    ex = _exec(db, a, [{"say": "hello "}, {"say": "world"}])
    assert run(ex.id) == "COMPLETED"
    db.expire_all()
    ex = db.get(Execution, ex.id)
    assert ex.output["text"] == "hello world" and ex.started_at and ex.ended_at and ex.runtime_id
    assert ex.input_tokens == 2 and ex.output_tokens == 2 and ex.is_mock is True
    assert _states(db, ex.id) == ["CREATED", "INITIALIZING", "READY", "RUNNING", "COMPLETED"]
    seqs = list(db.scalars(select(ExecutionEvent.seq).where(ExecutionEvent.execution_id == ex.id)
                           .order_by(ExecutionEvent.seq)))
    assert seqs == list(range(1, len(seqs) + 1))  # gapless ordering
    assert db.get(Agent, a.id).state == "IDLE"   # agent RUNNING -> COMPLETED -> IDLE


def test_illegal_transition_rejected(db):
    a = _agent(db, "illegal")
    ex = _exec(db, a, [{"say": "x"}])
    with pytest.raises(RuntimeError):
        service.set_status(db, ex, contract.TRANSITIONS and service.X.COMPLETED)


def test_tool_gate_default_deny_and_audit(db):
    called = []

    async def fake_repo_read(ex, args):
        called.append(args)
        return {"content": "file body"}

    service.register_tool("repo.read", fake_repo_read)
    a = _agent(db, "tools", tools=["repo.read"])
    ex = _exec(db, a, [{"tool": "repo.read", "args": {"path": "README.md"}}, {"tool": "files.write", "args": {}}])
    assert run(ex.id) == "FAILED"
    db.expire_all()
    ex = db.get(Execution, ex.id)
    assert called == [{"path": "README.md"}]
    assert ex.error_category == "policy" and "files.write" in ex.error
    assert "WAITING_FOR_TOOL" in _states(db, ex.id)
    assert db.scalar(select(AuditLog).where(AuditLog.action == "runtime.tool_denied", AuditLog.execution_id == ex.id))


def test_timeout(db):
    a = _agent(db, "slow", timeout=1)
    ex = _exec(db, a, [{"sleep": 3}])
    assert run(ex.id) == "FAILED"
    db.expire_all()
    assert db.get(Execution, ex.id).error_category == "timeout"


def test_cancel_running_execution(db):
    a = _agent(db, "cancel")
    ex = _exec(db, a, [{"say": "a"}, {"sleep": 0.3}, {"say": "b"}, {"sleep": 0.3}, {"say": "c"}])

    async def scenario():
        task = asyncio.create_task(service.run_execution(ex.id))
        await asyncio.sleep(0.4)
        from app.db import session_factory
        with session_factory()() as s:
            service.request_cancel(s, s.get(Execution, ex.id), "test")
        return await task

    assert asyncio.run(scenario()) == "CANCELLED"
    db.expire_all()
    out = db.get(Execution, ex.id).output or {}
    assert "c" not in "".join(out.get("parts", []))


def test_cancel_idle_execution_immediately(db):
    a = _agent(db, "cancel-idle")
    ex = _exec(db, a, [{"say": "never"}])
    service.request_cancel(db, ex, "test")
    assert ex.status == "CANCELLED"
    assert run(ex.id) == "CANCELLED"  # a late dispatch does nothing


def test_retry_with_backoff_then_success(db):
    a = _agent(db, "retry", retry={"max_attempts": 3, "backoff_seconds": 0.01, "backoff_multiplier": 2})
    ex = _exec(db, a, [{"say": "step1 "}, {"fail_until_attempt": 3}, {"say": "done"}])
    assert run(ex.id) == "COMPLETED"
    db.expire_all()
    ex = db.get(Execution, ex.id)
    assert ex.attempt == 3 and ex.output["text"] == "step1 done"  # step1 not repeated: resumed from checkpoint
    retries = db.scalars(select(ExecutionEvent).where(ExecutionEvent.execution_id == ex.id,
                                                      ExecutionEvent.type == "retry").order_by(ExecutionEvent.seq)).all()
    assert [r.payload["after_seconds"] for r in retries] == [0.01, 0.02]  # exponential backoff


def test_retry_budget_exhausted(db):
    a = _agent(db, "budget", retry={"max_attempts": 2, "backoff_seconds": 0, "backoff_multiplier": 1})
    ex = _exec(db, a, [{"fail": "transient"}])
    assert run(ex.id) == "FAILED"
    db.expire_all()
    assert db.get(Execution, ex.id).attempt == 2


def test_fatal_errors_are_not_retried(db):
    a = _agent(db, "fatal", retry={"max_attempts": 5, "backoff_seconds": 0, "backoff_multiplier": 1})
    ex = _exec(db, a, [{"fail": "fatal"}])
    assert run(ex.id) == "FAILED"
    db.expire_all()
    assert db.get(Execution, ex.id).attempt == 1


def test_resume_from_checkpoint_after_runtime_crash(db):
    a = _agent(db, "crash")
    ex = _exec(db, a, [{"say": "one "}, {"say": "two "}, {"say": "three"}])
    # simulate: two steps ran and were checkpointed, then the worker died while RUNNING
    service.set_status(db, ex, service.X.INITIALIZING)
    service.set_status(db, ex, service.X.READY)
    service.set_status(db, ex, service.X.RUNNING)
    db.add(Checkpoint(execution_id=ex.id, seq=2, state={"next_step": 2, "parts": ["one ", "two "]}, verified=True))
    ex.latest_checkpoint_seq, ex.runtime_id = 2, service.RUNTIME_ID
    db.commit()
    assert service.mark_orphans_terminated(db, service.RUNTIME_ID) >= 1
    assert db.get(Execution, ex.id).status == "TERMINATED"
    assert run(ex.id) == "COMPLETED"
    db.expire_all()
    ex = db.get(Execution, ex.id)
    assert ex.output["text"] == "one two three"
    assert "RECOVERING" in _states(db, ex.id)


def test_idempotency_and_concurrency_limit(db):
    a = _agent(db, "idem")
    e1 = _exec(db, a, [{"say": "x"}], idempotency_key="k-1")
    e2 = _exec(db, a, [{"say": "x"}], idempotency_key="k-1")
    assert e1.id == e2.id
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as err:
        _exec(db, a, [{"say": "y"}])  # concurrency_limit=1 and e1 is still CREATED
    assert err.value.detail == "agent_concurrency_limit"


def test_api_create_events_and_websocket_stream(admin_client, db):
    a = _agent(db, "api")
    r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"mock_script": [{"say": "hi"}]}})
    assert r.status_code == 201
    ex_id = r.json()["id"]
    # TestClient runs the background task before returning
    assert admin_client.get(f"/api/executions/{ex_id}").json()["status"] == "COMPLETED"
    evs = admin_client.get(f"/api/executions/{ex_id}/events").json()
    assert [e["seq"] for e in evs] == sorted(e["seq"] for e in evs)
    with admin_client.websocket_connect(f"/api/executions/{ex_id}/stream") as ws:
        got = []
        while True:
            m = ws.receive_json()
            got.append(m)
            if m["type"] == "end":
                break
    assert got[-1]["status"] == "COMPLETED" and [m["seq"] for m in got[:-1]] == [e["seq"] for e in evs]


def test_websocket_rejects_cross_origin_and_anonymous(admin_client, db, client):
    a = _agent(db, "ws-sec")
    ex_id = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {}}).json()["id"]
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect), admin_client.websocket_connect(
            f"/api/executions/{ex_id}/stream", headers={"origin": "https://evil.example"}) as ws:
        ws.receive_json()
