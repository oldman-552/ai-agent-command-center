"""EV-03 — failure categorization, recovery workflow record, agent and provider circuit breakers."""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.main  # noqa: F401
from app.connectivity.gateway.base import ProviderError
from app.models import Agent, AuditLog, CircuitBreaker, Execution, ExecutionEvent, Provider
from app.runtime import circuit, contract, service
from app.runtime.contract import RetryableError, ToolDenied
from app.runtime.engines.mock import MockEngine


@pytest.fixture(autouse=True)
def mock_engine(monkeypatch):
    monkeypatch.setitem(contract._ENGINES, "api", MockEngine)


def _agent(db, **kw) -> Agent:
    kw.setdefault("retry_policy", {"max_attempts": 2, "backoff_seconds": 0, "backoff_multiplier": 1})
    a = Agent(organization_id=1, name=f"R {uuid.uuid4().hex[:5]}", identifier=f"rec-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", concurrency_limit=10, timeout_seconds=30, **kw)
    db.add(a)
    db.commit()
    return a


def _run(db, agent, script) -> Execution:
    ex = service.create_execution(db, organization_id=1, agent=agent, actor="test", input={"mock_script": script})
    db.commit()
    asyncio.run(service.run_execution(ex.id))
    db.expire_all()
    return db.get(Execution, ex.id)


def test_failure_categories():
    assert service.categorize(ToolDenied("x")) == "policy"
    assert service.categorize(TimeoutError()) == "timeout"
    assert service.categorize(RetryableError("x")) == "transient"
    assert service.categorize(ProviderError("bad key")) == "provider"
    assert service.categorize(ValueError("boom")) == "error"
    assert service.RETRY_SAFE["transient"] and not service.RETRY_SAFE["policy"]


def test_recovery_report_preserves_evidence_and_escalates(db):
    a = _agent(db)
    ex = _run(db, a, [{"say": "partial "}, {"fail": "transient"}])
    assert ex.status == "FAILED" and ex.error_category == "transient" and ex.attempt == 2  # budget used, no loop
    assert ex.output["partial"] is True and ex.output["text"].startswith("partial ")  # partial result kept
    report = db.scalar(select(ExecutionEvent).where(ExecutionEvent.execution_id == ex.id,
                                                    ExecutionEvent.type == "recovery")).payload
    assert report["category"] == "transient" and report["retry_safe"] and report["attempts"] == 2
    assert report["retry_budget"] == 2 and report["outcome"] == "escalated" and report["evidence_events"] > 3
    assert db.scalar(select(AuditLog).where(AuditLog.action == "recovery.escalated", AuditLog.execution_id == ex.id))


def test_agent_circuit_opens_blocks_then_half_opens_and_closes(db, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "circuit_failure_threshold", 3)
    a = _agent(db, retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1})
    for _ in range(3):
        _run(db, a, [{"fail": "fatal"}])
    assert circuit.state(db, "agent", a.id) == "open"
    with pytest.raises(HTTPException) as e:
        service.create_execution(db, organization_id=1, agent=a, actor="test", input={})
    assert e.value.detail == "agent_circuit_open"
    from app.models import Task
    from app.orchestration.selection import select_agent
    t = Task(organization_id=1, title="x", required_skills=[a.identifier])  # only this agent could match
    a.skills = [a.identifier]
    db.add(t)
    db.commit()
    assert select_agent(db, t).agent is None  # open circuits are not assignable
    # policy denials do not count against health
    cb = db.scalar(select(CircuitBreaker).where(CircuitBreaker.scope == "agent", CircuitBreaker.ref_id == a.id))
    cb.opened_at = datetime.now(UTC) - timedelta(hours=1)  # cool-down elapsed
    db.commit()
    trial = _run(db, a, [{"say": "ok"}])  # the one half-open trial
    assert trial.status == "COMPLETED" and circuit.state(db, "agent", a.id) == "closed"
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.details["id"].as_integer() == a.id,
                                                           AuditLog.category == "runtime")))
    assert {"circuit.agent_opened", "circuit.agent_half_open", "circuit.agent_closed"} <= actions


def test_policy_denials_do_not_open_the_circuit(db, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "circuit_failure_threshold", 2)
    a = _agent(db)
    for _ in range(3):
        assert _run(db, a, [{"tool": "files.write", "args": {}}]).error_category == "policy"
    assert circuit.state(db, "agent", a.id) == "closed"


def test_provider_circuit_routes_to_fallback_without_calling_the_failing_provider(db, monkeypatch):
    from app.config import get_settings
    from app.connectivity.gateway import adapters
    from app.connectivity.gateway import service as gw

    monkeypatch.setattr(get_settings(), "circuit_failure_threshold", 2)
    monkeypatch.setitem(contract._ENGINES, "api", gw.ApiEngine)  # the real gateway engine
    primary = Provider(organization_id=1, name=f"P {uuid.uuid4().hex[:5]}", kind="mock", auth_type="none",
                       enabled=True, is_mock=True)
    backup = Provider(organization_id=1, name=f"B {uuid.uuid4().hex[:5]}", kind="mock", auth_type="none",
                      enabled=True, is_mock=True)
    db.add_all([primary, backup])
    db.commit()
    a = _agent(db, provider_id=primary.id, model="mock-model", fallback_provider_id=backup.id,
               fallback_model="mock-model")
    calls = []
    real = adapters.MockAdapter.stream

    async def stream(self, req):
        calls.append(self)
        async for ev in real(self, req):
            yield ev
    monkeypatch.setattr(adapters.MockAdapter, "stream", stream)
    # the primary has repeated outages → its circuit opens
    for _ in range(2):
        circuit.record_failure(db, "provider", primary.id, 1, "503")
    db.commit()
    assert circuit.state(db, "provider", primary.id) == "open"
    calls.clear()
    ex = service.create_execution(db, organization_id=1, agent=a, actor="test", input={"prompt": "hello"})
    db.commit()
    assert asyncio.run(service.run_execution(ex.id)) == "COMPLETED"
    assert len(calls) == 1  # only the fallback was called
    assert db.scalar(select(AuditLog).where(AuditLog.action == "gateway.circuit_skip", AuditLog.execution_id == ex.id))
