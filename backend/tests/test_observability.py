"""UX-05 (structured logs, traces, health of all services), UX-02 (live updates over WebSocket), FND-14 (Redis)."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid

import pytest
import redis
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import app.main  # noqa: F401
from app.config import get_settings
from app.observability import live, tracing
from app.observability.logs import JsonFormatter
from app.security.audit import log_activity

EXPORTER = InMemorySpanExporter()
tracing.add_exporter(EXPORTER)


def test_json_logs_carry_trace_ids_and_are_redacted():
    fmt = JsonFormatter("api")
    rec = logging.LogRecord("acc.test", logging.INFO, __file__, 1, "key sk-abcdefghijklmnop1234 used", (), None)
    rec.task_id = 7
    with tracing.tracer().start_as_current_span("unit"):
        out = json.loads(fmt.format(rec))
    assert out["service"] == "api" and out["level"] == "INFO" and out["task_id"] == 7
    assert len(out["trace_id"]) == 32 and len(out["span_id"]) == 16
    assert "sk-abcdefghijklmnop1234" not in out["message"]


def test_http_requests_and_executions_produce_spans(admin_client, db):
    EXPORTER.clear()
    admin_client.get("/api/agents")
    names = [s.name for s in EXPORTER.get_finished_spans()]
    assert any("/api/agents" in n for n in names), names
    from app.models import Agent
    a = Agent(organization_id=1, name=f"O {uuid.uuid4().hex[:5]}", identifier=f"ob-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api",
              provider_id=next(x["id"] for x in admin_client.get("/api/providers").json() if x["kind"] == "mock"),
              model="mock-model")
    db.add(a)
    db.commit()
    EXPORTER.clear()
    r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"prompt": "hi"}})
    assert r.status_code == 201
    spans = {s.name: s for s in EXPORTER.get_finished_spans()}
    assert "execution.run" in spans and spans["execution.run"].attributes["acc.execution_id"] == r.json()["id"]
    assert spans["execution.run"].attributes["acc.status"] == "COMPLETED"


def test_live_updates_are_published_after_commit_only(db):
    r = redis.Redis.from_url(get_settings().redis_url)
    ps = r.pubsub()
    ps.subscribe(live.channel(1))
    ps.get_message(timeout=1)  # subscribe confirmation
    log_activity(db, "test.rolled_back", category="tasks", organization_id=1, task_id=1)
    db.rollback()
    assert ps.get_message(timeout=0.5) is None  # nothing for work that never happened
    log_activity(db, "test.committed", category="tasks", organization_id=1)
    db.commit()
    msg = ps.get_message(timeout=2)
    assert msg and json.loads(msg["data"]) == {"topic": "tasks", "action": "test.committed"}
    ps.close()


def test_websocket_requires_session_and_csrf_then_streams_org_events(client, admin_client, db):
    from starlette.websockets import WebSocketDisconnect

    from app.models import Organization
    other = Organization(name=f"WS {uuid.uuid4().hex[:6]}", slug=f"ws-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.commit()
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"csrf": "wrong"}))
        ws.receive_text()
    assert e.value.code == 4403  # signed in, but no CSRF proof
    csrf = client.cookies.get("acc_csrf")
    with client.websocket_connect("/api/ws") as ws:
        ws.send_text(json.dumps({"csrf": csrf}))
        assert json.loads(ws.receive_text())["topic"] == "hello"

        other_id = other.id

        def later():
            time.sleep(0.3)
            from app.db import session_factory
            with session_factory()() as s:
                log_activity(s, "test.other_org", category="approvals", organization_id=other_id)
                s.commit()
                log_activity(s, "test.live", category="approvals", organization_id=1)
                s.commit()
        threading.Thread(target=later).start()
        got = json.loads(ws.receive_text())
        assert got == {"topic": "approvals", "action": "test.live"}  # never another organization's events


def test_websocket_rejects_anonymous(client):
    from starlette.websockets import WebSocketDisconnect
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect("/api/ws") as ws:
        ws.receive_text()
    assert e.value.code == 4401


def test_health_reports_every_service(client):
    live.client().delete(*list(live.client().scan_iter("acc:heartbeat:*")) or ["none"])
    body = client.get("/api/health").json()
    s = body["services"]
    assert s["database"] == "ok" and s["redis"] == "ok" and s["rabbitmq"] == "ok"
    assert s["workers"]["status"] == "none" and body["status"] == "degraded"  # no worker → degraded, not down
    assert s["browser_bridge"]["status"] in ("ok", "not_running")
    live.heartbeat("worker", {"roles": ["executions"]})
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["services"]["workers"]["instances"][0]["roles"] == ["executions"]
