"""M10 — MQ-01..MQ-04 against a live RabbitMQ (vhost acc_test) and PostgreSQL. No broker mocks."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import aio_pika
import pytest
from sqlalchemy import select, text

import app.main  # noqa: F401  — registers real engines first, so the mock rebinding below sticks
from app.bus import topology as topo
from app.bus.consumer import Consumer, Fatal, connect
from app.bus.contract import Envelope, MessageType, execution_run
from app.bus.outbox import Relay, amqp_message, enqueue
from app.config import get_settings
from app.db import get_engine
from app.models import Agent, Execution, ExecutionEvent, OutboxMessage, ProcessedMessage
from app.runtime import contract
from app.runtime.engines.mock import MockEngine
from tests.conftest import TEST_AMQP

BACKEND = Path(__file__).resolve().parent.parent


# ---------------- fixtures ----------------
@pytest.fixture(autouse=True)
def _clean_bus():
    """Each test starts with empty queues and outbox (the outbox is global, not per test)."""
    with get_engine().begin() as c:
        c.execute(text("UPDATE outbox_messages SET published_at = now() WHERE published_at IS NULL"))

    async def purge():
        conn = await connect(TEST_AMQP)
        ch = await conn.channel()
        t = await topo.declare(ch)
        for q in [t.dlq, *t.work.values()]:
            await q.purge()
        for tier, _ in topo.RETRY_TIERS:
            await (await ch.get_queue(topo.retry_exchange(tier))).purge()
        await conn.close()
    asyncio.run(purge())


@pytest.fixture()
def bus_mode(monkeypatch):
    monkeypatch.setenv("EXECUTION_DISPATCH", "bus")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def mock_api_engine(monkeypatch):
    monkeypatch.setitem(contract._ENGINES, "api", MockEngine)


async def channel():
    conn = await connect(TEST_AMQP)
    return conn, await conn.channel(publisher_confirms=True, on_return_raises=True)


def env_for(key: str | None = None, **kw) -> Envelope:
    base = {"message_type": MessageType.TASK_UPDATED, "sender": "test", "recipient": "*", "organization_id": 1,
                "correlation_id": "corr-1", "idempotency_key": key or f"k-{uuid.uuid4()}", "payload": {"n": 1}}
    base.update(kw)
    return Envelope(**base)


async def get_one(queue, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        m = await queue.get(no_ack=True, fail=False)
        if m:
            return m
        await asyncio.sleep(0.05)
    return None


async def wait_for(pred, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.1)
    return pred()


# ---------------- MQ-01 topology ----------------
async def test_topology_declared_with_dlq_priorities_and_specialty_routing():
    conn, ch = await channel()
    t = await topo.declare(ch)
    await topo.declare(ch)  # idempotent re-declare (every process does it at start)
    for name in (topo.COMMANDS, topo.EVENTS, topo.DLX, *[topo.retry_exchange(x) for x, _ in topo.RETRY_TIERS]):
        await ch.get_exchange(name, ensure=True)  # passive: raises if missing
    assert set(t.work) == {"api", "cli", "browser", "a2a", "mock", "orchestrator", "orchestrator-events"}
    # each command reaches only its specialty queue
    for engine in ("api", "cli"):
        await t.commands.publish(amqp_message(env_for()), routing_key=topo.run_routing_key(engine), mandatory=True)
    assert await get_one(t.work["api"]) and await get_one(t.work["cli"])
    assert await get_one(t.work["browser"], timeout=0.5) is None
    # unroutable commands are refused at publish time, not silently dropped
    with pytest.raises(aio_pika.exceptions.DeliveryError):
        await t.commands.publish(amqp_message(env_for()), routing_key="execution.run.nope", mandatory=True)
    # priority: a later high-priority message is delivered first
    await t.commands.publish(amqp_message(env_for(priority=1, payload={"p": 1})), routing_key="execution.run.api")
    await t.commands.publish(amqp_message(env_for(priority=9, payload={"p": 9})), routing_key="execution.run.api")
    await asyncio.sleep(0.2)
    first = await get_one(t.work["api"])
    assert Envelope.from_bytes(first.body).payload == {"p": 9}
    # rejected messages land in the DLQ
    await t.commands.publish(amqp_message(env_for()), routing_key="execution.run.api")
    m = await t.work["api"].get(timeout=5)
    await m.reject(requeue=False)
    assert await get_one(t.dlq)
    await conn.close()


# ---------------- MQ-02 contract + outbox + retries ----------------
def test_contract_fields_and_validation():
    e = execution_run(execution_id="ex-1", organization_id=1, engine="api", sender="user:1", reason="create")
    for f in ("message_id", "sender", "recipient", "project_id", "task_id", "correlation_id", "message_type",
              "payload", "priority", "timestamp", "schema_version", "idempotency_key"):
        assert f in e.model_dump()
    assert e.is_command and e.category == "execution" and e.correlation_id == "ex-1"
    assert Envelope.from_bytes(e.to_bytes()) == e
    with pytest.raises(ValueError):
        Envelope.from_bytes(e.to_bytes().replace(b'"schema_version":"1"', b'"schema_version":"99"'))
    with pytest.raises(ValueError):
        env_for(priority=10)
    m = amqp_message(e)
    assert (m.message_id, m.correlation_id, m.priority, m.type) == (e.message_id, "ex-1", 5, "execution.run")
    assert m.delivery_mode == aio_pika.DeliveryMode.PERSISTENT and m.headers["schema_version"] == "1"


async def test_outbox_is_transactional_and_relay_publishes_with_confirms(db):
    e1, e2 = env_for(), env_for()
    enqueue(db, e1)
    db.rollback()  # the state change failed → its message must not exist
    enqueue(db, e2)
    db.commit()
    assert db.scalar(select(OutboxMessage).where(OutboxMessage.message_id == e1.message_id)) is None
    conn, ch = await channel()
    sub = await topo.declare_event_subscription(ch, f"test.sub.{uuid.uuid4().hex[:6]}", ["task.#"])
    assert await Relay(ch).publish_pending() == 1
    row = db.scalar(select(OutboxMessage).where(OutboxMessage.message_id == e2.message_id))
    db.refresh(row)
    assert row.published_at is not None
    m = await get_one(sub)
    assert Envelope.from_bytes(m.body).message_id == e2.message_id and m.message_id == e2.message_id
    assert await Relay(ch).publish_pending() == 0  # never published twice
    await sub.delete(if_unused=False, if_empty=False)
    await conn.close()


async def run_consumer(consumer: Consumer, envs: list[Envelope], until, timeout=15.0, raw: list[bytes] = ()):
    conn, ch = await channel()
    await topo.declare(ch)
    cch = await conn.channel(publisher_confirms=True)
    await consumer.start(cch)
    ex = await ch.get_exchange(topo.COMMANDS)
    for e in envs:
        await ex.publish(amqp_message(e), routing_key="execution.run.mock")
    for body in raw:
        await ex.publish(aio_pika.Message(body), routing_key="execution.run.mock")
    ok = await wait_for(until, timeout)
    await conn.close()
    return ok


async def test_retry_with_delay_then_success():
    calls = []

    async def flaky(env):
        calls.append(env.attempt)
        if env.attempt == 1:
            raise ConnectionError("db briefly down")
    c = Consumer(name=f"t.retry.{uuid.uuid4().hex[:6]}", queue=topo.work_queue("mock"), handler=flaky)
    t0 = time.monotonic()
    assert await run_consumer(c, [env_for()], lambda: c.stats["done"] == 1, timeout=20)
    assert calls == [1, 2] and c.stats["retried"] == 1
    assert time.monotonic() - t0 >= topo.RETRY_TIERS[0][1] - 0.5  # waited in the delay tier


async def test_timeout_poison_fatal_and_exhausted_go_to_dlq():
    async def slow(env):
        if env.payload.get("kind") == "fatal":
            raise Fatal("bad payload")
        if env.payload.get("kind") == "slow":
            await asyncio.sleep(5)
        raise RuntimeError("always fails")
    c = Consumer(name=f"t.dlq.{uuid.uuid4().hex[:6]}", queue=topo.work_queue("mock"), handler=slow,
                 max_attempts=1, timeout_s=0.3)
    envs = [env_for(payload={"kind": "fatal"}), env_for(payload={"kind": "slow"}), env_for(payload={"kind": "x"}),
            env_for(expires_at=env_for().timestamp)]  # already expired
    assert await run_consumer(c, envs, lambda: c.stats["dead_lettered"] == 5,
                              raw=[b"not json"])
    conn, ch = await channel()
    dlq = await ch.get_queue(topo.DLQ)
    got = 0
    while await get_one(dlq, timeout=1.0):
        got += 1
    assert got == 5
    await conn.close()


# ---------------- MQ-03 idempotency ----------------
async def test_duplicate_delivery_processed_once(db):
    seen = []

    async def h(env):
        seen.append(env.message_id)
    name = f"t.idem.{uuid.uuid4().hex[:6]}"
    c = Consumer(name=name, queue=topo.work_queue("mock"), handler=h)
    e = env_for(key="same-key")
    dup = e.model_copy(update={"message_id": str(uuid.uuid4())})  # re-sent by a producer retry
    assert await run_consumer(c, [e, e, dup], lambda: c.stats["done"] + c.stats["duplicate"] + c.stats["deferred"] >= 3)
    await asyncio.sleep(0.5)
    assert len(seen) == 1
    assert db.scalar(select(ProcessedMessage).where(ProcessedMessage.consumer == name)).outcome == "done"


async def test_busy_work_key_is_deferred_not_run_twice():
    """A second message for work another live worker holds is deferred, never run concurrently."""
    ran = []

    async def h(env):
        ran.append(env.idempotency_key)
    name = f"t.lock.{uuid.uuid4().hex[:6]}"
    c = Consumer(name=name, queue=topo.work_queue("mock"), handler=h, work_key=lambda e: "execution:busy")
    holder = get_engine().connect()
    holder.execute(text("SELECT pg_advisory_lock(hashtextextended(:k, 0))"), {"k": f"{name}:execution:busy"})
    holder.commit()
    try:
        assert await run_consumer(c, [env_for()], lambda: c.stats["deferred"] == 1)
        assert ran == []
    finally:
        holder.close()  # the "other worker" goes away → its lock is released


# ---------------- end-to-end + MQ-04 recovery ----------------
WORKER = (
    "import asyncio,sys;import app.worker as w;"  # import first: it registers the real engines
    "from app.runtime import contract;from app.runtime.engines.mock import MockEngine;"
    "contract._ENGINES['api']=MockEngine;"  # then TEST-ONLY rebinding, same as the runtime tests
    "sys.exit(asyncio.run(w.main()))"
)


def start_worker(**extra_env: str) -> subprocess.Popen:
    env = {**os.environ, "EXECUTION_DISPATCH": "bus", "RABBITMQ_URL": TEST_AMQP, "PYTHONUNBUFFERED": "1", **extra_env}
    p = subprocess.Popen([sys.executable, "-c", WORKER], cwd=BACKEND, env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        line = p.stdout.readline()
        if "WORKER_READY" in line:
            return p
        if p.poll() is not None:
            break
    p.kill()
    raise AssertionError("worker did not start")


def stop_worker(p: subprocess.Popen, hard=False) -> None:
    p.kill() if hard else p.terminate()
    p.wait(timeout=15)


def _agent(db, name, **kw) -> Agent:
    a = Agent(organization_id=1, name=name, identifier=f"bus-{name}-{uuid.uuid4().hex[:6]}", role="backend",
              state="IDLE", execution_engine="api", tool_permissions=[], timeout_seconds=60,
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1}, **kw)
    db.add(a)
    db.commit()
    return a


def _status(db, ex_id):
    db.expire_all()
    return db.get(Execution, ex_id).status


async def test_api_publishes_and_worker_runs_execution(admin_client, db, bus_mode, mock_api_engine):
    a = _agent(db, "e2e")
    conn, ch = await channel()
    await topo.declare(ch)
    events = await topo.declare_event_subscription(ch, f"test.events.{uuid.uuid4().hex[:6]}", ["execution.#"])
    w = start_worker()
    try:
        r = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"mock_script": [{"say": "hi"}]}})
        assert r.status_code == 201 and r.json()["status"] == "CREATED"  # not run in the API process
        ex_id = r.json()["id"]
        assert db.scalar(select(OutboxMessage).where(OutboxMessage.envelope["correlation_id"].astext == ex_id))
        assert await wait_for(lambda: _status(db, ex_id) == "COMPLETED")
        types = []
        while (m := await get_one(events, timeout=3)) and len(types) < 2:
            e = Envelope.from_bytes(m.body)
            assert e.correlation_id == ex_id
            types.append(e.message_type)
        assert types == ["execution.started", "execution.finished"]
    finally:
        stop_worker(w)
        await events.delete(if_unused=False, if_empty=False)
        await conn.close()


async def test_worker_killed_mid_execution_is_recovered_by_another(admin_client, db, bus_mode, mock_api_engine):
    a = _agent(db, "crash")
    script = [{"say": "one "}, {"sleep": 1.5}, {"say": "two "}, {"sleep": 1.5}, {"say": "three"}]
    w1 = start_worker()
    ex_id = admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"mock_script": script}}).json()["id"]
    assert await wait_for(lambda: _status(db, ex_id) == "RUNNING")
    await asyncio.sleep(1.0)  # at least one step checkpointed
    stop_worker(w1, hard=True)  # kill -9: no ack, no cleanup
    assert _status(db, ex_id) == "RUNNING"
    w2 = start_worker()  # different pid → different runtime id; recovers via redelivery + free work lock
    try:
        assert await wait_for(lambda: _status(db, ex_id) == "COMPLETED", timeout=30)
    finally:
        stop_worker(w2)
    ex = db.get(Execution, ex_id)
    assert ex.output["text"] == "one two three"  # resumed from checkpoint: no step lost or duplicated
    states = [e.payload["to"] for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == ex_id, ExecutionEvent.type == "state").order_by(ExecutionEvent.seq))]
    assert "TERMINATED" in states and "RECOVERING" in states


RMQCTL = shutil.which("rabbitmqctl")


def _ctl(*args):
    subprocess.run([RMQCTL, *args], check=True, capture_output=True, timeout=60)


@pytest.mark.skipif(not RMQCTL or os.geteuid() != 0, reason="needs local broker control (rabbitmqctl as root)")
async def test_broker_restart_loses_nothing(admin_client, db, bus_mode, mock_api_engine):
    a = _agent(db, "broker")
    w = start_worker()
    try:
        _ctl("stop_app")
        # created while the broker is down: the API still answers; the command waits in the outbox
        ex_id = admin_client.post("/api/executions", json={"agent_id": a.id,
                                                           "input": {"mock_script": [{"say": "ok"}]}}).json()["id"]
        await asyncio.sleep(1.5)
        assert _status(db, ex_id) == "CREATED"
        row = db.scalar(select(OutboxMessage).where(OutboxMessage.envelope["correlation_id"].astext == ex_id))
        assert row.published_at is None
        _ctl("start_app")
        _ctl("await_startup")
        assert await wait_for(lambda: _status(db, ex_id) == "COMPLETED", timeout=60)  # worker reconnected
    finally:
        _ctl("start_app")
        stop_worker(w)
    # persistent messages survive a broker restart while queued
    conn, ch = await channel()
    t = await topo.declare(ch)
    await t.commands.publish(amqp_message(env_for(payload={"keep": True})), routing_key="execution.run.cli")
    await conn.close()
    _ctl("stop_app")
    _ctl("start_app")
    _ctl("await_startup")
    conn, ch = await channel()
    m = await get_one(await ch.get_queue(topo.work_queue("cli")))
    assert m and Envelope.from_bytes(m.body).payload == {"keep": True}
    await conn.close()


async def test_a2a_task_runs_through_bus_and_worker(admin_client, client, db, bus_mode, mock_api_engine):
    """A2A SendMessage in production dispatch mode: outbox → RabbitMQ → worker → blocking answer."""
    a = _agent(db, "a2a", concurrency_limit=5)
    token = admin_client.post("/api/a2a/keys", json={"name": "bus", "agent_ids": [a.id]}).json()["token"]
    w = start_worker()
    try:
        r = client.post(f"/api/a2a/agents/{a.identifier}",
                        headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"},
                        json={"jsonrpc": "2.0", "id": 7, "method": "SendMessage", "params": {"message": {
                            "messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": "via bus"}]}}})
        task = r.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["artifacts"][0]["parts"][0]["text"].endswith("via bus")
        assert db.get(Execution, task["id"]).runtime_id != service_runtime_id()  # ran in the worker, not the API
    finally:
        stop_worker(w)


def service_runtime_id():
    from app.runtime import service
    return service.RUNTIME_ID


# ---------------- M6 over the bus: planning, fan-out, review, worker restart mid-workflow ----------------
async def test_workflow_runs_end_to_end_over_bus_and_survives_worker_kill(admin_client, db, bus_mode, mock_api_engine):
    from tests.test_orchestration import DIAMOND, agent

    crew = [agent(db, "project_manager", tools=["tasks.plan"], priority=1),
            *[agent(db, r) for r in ("architect", "backend", "frontend", "qa")]]
    proj = admin_client.post("/api/projects", json={"name": f"Bus {uuid.uuid4().hex[:6]}",
                                                    "agent_ids": [a.id for a in crew]}).json()
    wf = admin_client.post("/api/workflows", json={"project_id": proj["id"], "title": "diamond",
                                                   "objective": DIAMOND}).json()
    assert wf["status"] == "planning"

    def tasks():
        return {t["title"].removeprefix("[MOCK] "): t
                for t in admin_client.get(f"/api/workflows/{wf['id']}").json()["tasks"]}

    def in_review(*names):
        ts = tasks()
        return all(ts.get(n, {}).get("status") == "REVIEW" for n in names)

    def approve(*names):
        ts = tasks()
        for n in names:
            r = admin_client.post(f"/api/tasks/{ts[n]['id']}/review", json={"decision": "approve"})
            assert r.status_code == 200, r.text

    w = start_worker()
    try:
        assert await wait_for(lambda: in_review("Design API"), timeout=30)
        approve("Design API")
        assert await wait_for(lambda: in_review("Build backend", "Build frontend"), timeout=30)
    finally:
        stop_worker(w, hard=True)  # orchestrator + executors die mid-workflow
    plan = admin_client.get(f"/api/workflows/{wf['id']}").json()["plan"]
    assert len(plan["rounds"][1]["dispatched"]) == 2  # fan-out: both branches dispatched in one round
    approve("Build backend", "Build frontend")  # decisions recorded while no worker is alive
    assert tasks()["Integrate"]["status"] == "PLANNED"
    w2 = start_worker()
    try:
        assert await wait_for(lambda: in_review("Integrate"), timeout=30)  # resumed from DB + outbox
        approve("Integrate")
        assert await wait_for(lambda: admin_client.get(f"/api/workflows/{wf['id']}").json()["status"] == "completed",
                              timeout=30)
    finally:
        stop_worker(w2)
    done = admin_client.get(f"/api/workflows/{wf['id']}").json()
    assert all(t["status"] == "COMPLETED" and t["attempts"] == 1 for t in done["tasks"])  # nothing ran twice



# ---------------- EV-04: graceful shutdown ----------------
async def test_sigterm_lets_short_work_finish_and_hands_off_long_work(admin_client, db, bus_mode, mock_api_engine):
    import signal

    a = _agent(db, "shutdown", concurrency_limit=5)
    short = [{"say": "quick"}, {"sleep": 1.0}, {"say": " done"}]
    long = [{"say": "one "}, {"sleep": 1.0}, {"say": "two "}, {"sleep": 6}, {"say": "three"}]
    w = start_worker(SHUTDOWN_GRACE_SECONDS="3")
    try:
        ids = [admin_client.post("/api/executions", json={"agent_id": a.id, "input": {"mock_script": s}}).json()["id"]
               for s in (short, long)]
        assert await wait_for(lambda: all(_status(db, i) == "RUNNING" for i in ids))
        await asyncio.sleep(1.3)  # the long one has checkpointed "one two "
        w.send_signal(signal.SIGTERM)
        out, _ = w.communicate(timeout=30)
    finally:
        if w.poll() is None:
            w.kill()
    assert w.returncode == 0 and "worker stopped cleanly" in out and "handing off 1" in out
    assert _status(db, ids[0]) == "COMPLETED"  # finished inside the grace period
    db.expire_all()
    handed = db.get(Execution, ids[1])
    assert handed.status == "TERMINATED" and handed.output["partial"] and handed.output["text"] == "one two "
    assert db.scalar(select(ExecutionEvent).where(ExecutionEvent.execution_id == ids[1],
                                                  ExecutionEvent.type == "shutdown"))
    w2 = start_worker()  # the un-acked message is redelivered; the new worker resumes from the checkpoint
    try:
        assert await wait_for(lambda: _status(db, ids[1]) == "COMPLETED", timeout=30)
    finally:
        stop_worker(w2)
    db.expire_all()
    assert db.get(Execution, ids[1]).output["text"] == "one two three"  # nothing lost, nothing repeated
