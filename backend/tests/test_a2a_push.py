"""A2A push notifications: config methods, webhook deliveries (payload, headers, order, retries, disabling) and the
SSRF guard (spec §13.2)."""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.connectivity.a2a import push
from app.models import A2APushConfig, A2APushDelivery, OutboxMessage
from app.runtime.engines.mock import MOCK_LABEL
from tests import test_a2a as base
from tests.test_a2a import V, msg, rpc  # noqa: F401

exposed = base.exposed  # fixtures shared with the A2A server tests
mock_api_engine = base.mock_api_engine


class Receiver:
    """A local webhook endpoint that records requests and answers with the configured status."""

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
        rec = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                rec.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
                                     "body": json.loads(body)})
                self.send_response(rec.status)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def receiver(monkeypatch):
    monkeypatch.setenv("A2A_PUSH_ALLOW_PRIVATE", "true")  # the receiver is on loopback: allowed for this test only
    get_settings.cache_clear()
    r = Receiver()
    yield r
    r.close()
    get_settings.cache_clear()


@pytest.fixture
def bus_mode(monkeypatch, db):
    monkeypatch.setenv("EXECUTION_DISPATCH", "bus")  # no worker: the task waits until the test moves it
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    db.query(OutboxMessage).filter(OutboxMessage.published_at.is_(None)).update(
        {"published_at": OutboxMessage.created_at})
    db.commit()


def deliver():
    return asyncio.run(push.deliver_due())


def test_card_advertises_push(client, exposed):
    a, token, _ = exposed
    card = client.get(f"/api/a2a/agents/{a.identifier}/.well-known/agent-card.json",
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert card["capabilities"]["pushNotifications"] is True


def test_send_message_with_push_config_delivers_status_and_final_task(client, exposed, receiver, db):
    a, token, _ = exposed
    p = msg("notify me", contextId="ctx-p")
    p["configuration"] = {"taskPushNotificationConfig": {
        "url": receiver.url, "token": "tok-123", "authentication": {"scheme": "Bearer", "credentials": "cred-xyz"}}}
    task = rpc(client, a, token, "SendMessage", p)["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert deliver() >= 2
    bodies = [r["body"] for r in receiver.requests]
    kinds = [next(iter(b)) for b in bodies]
    assert kinds[-1] == "task" and all(k == "statusUpdate" for k in kinds[:-1])  # StreamResponse, one per POST
    assert bodies[-1]["task"]["id"] == task["id"] and bodies[-1]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert bodies[-1]["task"]["artifacts"][0]["parts"][0]["text"] == f"{MOCK_LABEL}notify me"
    assert {b["statusUpdate"]["contextId"] for b in bodies[:-1]} == {"ctx-p"}
    h = receiver.requests[-1]["headers"]
    assert h["authorization"] == "Bearer cred-xyz" and h["x-a2a-notification-token"] == "tok-123"
    assert h["content-type"] == "application/a2a+json"
    assert deliver() == 0  # nothing is sent twice
    # secrets are write-only
    cfgs = rpc(client, a, token, "ListTaskPushNotificationConfigs", {"taskId": task["id"]})["result"]["configs"]
    assert len(cfgs) == 1 and cfgs[0]["authentication"] == {"scheme": "Bearer"}
    assert "token" not in cfgs[0] and "cred-xyz" not in json.dumps(cfgs) and cfgs[0]["metadata"]["tokenSet"] is True
    row = db.get(A2APushConfig, cfgs[0]["id"])
    assert "cred-xyz" not in (row.credentials_encrypted or "") and "tok-123" not in (row.token_encrypted or "")


def test_config_methods_on_a_running_task(client, exposed, receiver, bus_mode, db):
    a, token, _ = exposed
    p = msg("later")
    p["configuration"] = {"returnImmediately": True}
    t = rpc(client, a, token, "SendMessage", p)["result"]["task"]
    created = rpc(client, a, token, "CreateTaskPushNotificationConfig",
                  {"taskId": t["id"], "id": "cfg-1", "url": receiver.url})["result"]
    assert created == {"id": "cfg-1", "taskId": t["id"], "url": receiver.url,
                       "metadata": {"tokenSet": False, "consecutiveFailures": 0}}
    assert rpc(client, a, token, "GetTaskPushNotificationConfig", {"taskId": t["id"], "id": "cfg-1"})["result"] == created
    assert rpc(client, a, token, "GetTaskPushNotificationConfig",
               {"taskId": t["id"], "id": "nope"})["error"]["code"] == -32001
    for i in range(3):
        rpc(client, a, token, "CreateTaskPushNotificationConfig", {"taskId": t["id"], "url": f"{receiver.url}?n={i}"})
    page = rpc(client, a, token, "ListTaskPushNotificationConfigs", {"taskId": t["id"], "pageSize": 2})["result"]
    assert len(page["configs"]) == 2 and page["nextPageToken"]
    rest = rpc(client, a, token, "ListTaskPushNotificationConfigs",
               {"taskId": t["id"], "pageSize": 2, "pageToken": page["nextPageToken"]})["result"]
    assert len(rest["configs"]) == 2 and rest["nextPageToken"] == ""
    assert rpc(client, a, token, "DeleteTaskPushNotificationConfig", {"taskId": t["id"], "id": "cfg-1"})["result"] == {}
    assert len(rpc(client, a, token, "ListTaskPushNotificationConfigs", {"taskId": t["id"]})["result"]["configs"]) == 3
    # cancelling the task notifies every remaining config with the final task
    assert rpc(client, a, token, "CancelTask", {"id": t["id"]})["result"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert deliver() == 3
    assert {r["path"] for r in receiver.requests} == {f"/hook?n={i}" for i in range(3)}
    assert all(r["body"]["task"]["status"]["state"] == "TASK_STATE_CANCELED" for r in receiver.requests)
    # nothing to configure on a final task
    assert rpc(client, a, token, "CreateTaskPushNotificationConfig",
               {"taskId": t["id"], "url": receiver.url})["error"]["code"] == -32004


def test_failed_deliveries_retry_with_backoff_in_order_and_disable_the_config(client, exposed, receiver, bus_mode, db):
    a, token, _ = exposed
    p = msg("flaky")
    p["configuration"] = {"returnImmediately": True}
    t = rpc(client, a, token, "SendMessage", p)["result"]["task"]
    c = rpc(client, a, token, "CreateTaskPushNotificationConfig", {"taskId": t["id"], "url": receiver.url})["result"]
    rpc(client, a, token, "CancelTask", {"id": t["id"]})
    receiver.status = 503
    assert deliver() == 0
    d = db.scalars(select(A2APushDelivery).where(A2APushDelivery.config_id == c["id"])).one()
    db.refresh(d)
    assert d.attempts == 1 and d.last_status == 503 and d.next_attempt_at > datetime.now(UTC)
    assert deliver() == 0 and len(receiver.requests) == 1  # backoff: not retried before it is due
    receiver.status = 200
    d.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert deliver() == 1
    db.refresh(d)
    assert d.delivered_at is not None and d.attempts == 2
    cfg = db.get(A2APushConfig, c["id"])
    db.refresh(cfg)
    assert cfg.consecutive_failures == 0
    # repeated failures disable the config; further events are not attempted
    cfg.consecutive_failures = push.MAX_CONSECUTIVE_FAILURES - 1
    db.add(A2APushDelivery(config_id=cfg.id, payload={"task": {"id": t["id"]}}, next_attempt_at=datetime.now(UTC),
                           created_at=datetime.now(UTC)))
    db.commit()
    receiver.status = 500
    deliver()
    db.refresh(cfg)
    assert cfg.disabled_at is not None and "HTTP 500" in cfg.last_error
    meta = rpc(client, a, token, "GetTaskPushNotificationConfig", {"taskId": t["id"], "id": cfg.id})["result"]["metadata"]
    assert "disabledAt" in meta


def test_another_key_cannot_see_or_delete_configs(client, exposed, receiver, bus_mode, admin_client):
    a, token, _ = exposed
    p = msg("mine")
    p["configuration"] = {"returnImmediately": True}
    t = rpc(client, a, token, "SendMessage", p)["result"]["task"]
    c = rpc(client, a, token, "CreateTaskPushNotificationConfig", {"taskId": t["id"], "url": receiver.url})["result"]
    other = admin_client.post("/api/a2a/keys", json={"name": "other", "agent_ids": [a.id]}).json()["token"]
    for method in ("GetTaskPushNotificationConfig", "DeleteTaskPushNotificationConfig"):
        assert rpc(client, a, other, method, {"taskId": t["id"], "id": c["id"]})["error"]["code"] == -32001
    assert rpc(client, a, other, "ListTaskPushNotificationConfigs", {"taskId": t["id"]})["error"]["code"] == -32001


@pytest.mark.parametrize("url,ok", [
    ("http://example.com/h", False),          # https only
    ("https://user:pw@example.com/h", False),  # no credentials in the URL
    ("https://127.0.0.1/h", False),
    ("https://10.1.2.3/h", False),
    ("https://172.16.0.5/h", False),
    ("https://192.168.1.1/h", False),
    ("https://169.254.169.254/latest", False),  # cloud metadata (link-local)
    ("https://[::1]/h", False),
    ("https://[::ffff:127.0.0.1]/h", False),     # IPv4-mapped loopback
    ("https://localhost/h", False),
    ("https://printer.local/h", False),
    ("https://0.0.0.0/h", False),
    ("ftp://example.com/h", False),
    ("https://8.8.8.8/hook", True),              # a public address
])
def test_ssrf_guard(url, ok):
    if ok:
        push.check_url(url)
    else:
        with pytest.raises(push.PushConfigError):
            push.check_url(url)


def test_private_webhook_refused_over_rpc_by_default(client, exposed):
    a, token, _ = exposed
    p = msg("ssrf")
    p["configuration"] = {"taskPushNotificationConfig": {"url": "https://127.0.0.1:9/h"}}
    r = rpc(client, a, token, "SendMessage", p)
    assert r["error"]["code"] == -32602 and "public" in r["error"]["message"]
    p = msg("ssrf2")
    p["configuration"] = {"taskPushNotificationConfig": {"url": "https://example.com/h", "taskId": "x"}}
    assert rpc(client, a, token, "SendMessage", p)["error"]["code"] == -32602


def test_old_deliveries_are_pruned(client, exposed, receiver, db):
    a, token, _ = exposed
    p = msg("prune")
    p["configuration"] = {"taskPushNotificationConfig": {"url": receiver.url}}
    task = rpc(client, a, token, "SendMessage", p)["result"]["task"]
    deliver()
    cfg = db.scalars(select(A2APushConfig).where(A2APushConfig.execution_id == task["id"])).one()
    rows = lambda: db.scalars(select(A2APushDelivery).where(A2APushDelivery.config_id == cfg.id)).all()
    assert rows() and all(r.delivered_at for r in rows())
    assert push.prune() == 0 or rows()  # recent rows stay
    assert rows()
    push.prune(datetime.now(UTC) + push.RETENTION + timedelta(minutes=1))
    db.expire_all()
    assert rows() == []
