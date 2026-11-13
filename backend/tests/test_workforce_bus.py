"""WF-08 / WF-13 over the real bus: the API process, a real worker process, RabbitMQ and PostgreSQL.

Nothing is driven by hand here: the worker process plans with the Manager, sends the Automatic task to the worker's
provider, reviews, offers the Human-Assisted task, and consolidates — from the messages the API wrote to the outbox.
The worker is killed half-way and a second one finishes the objective.

Tier: local integration. The Manager and the Automatic worker use the mock provider; no Claude, no Arena.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.bus import topology as topo
from app.bus.consumer import connect
from app.config import get_settings
from app.db import get_engine
from app.models import Execution, ManagerStep, Task, WorkforceReport
from tests import wf as h
from tests.conftest import TEST_AMQP

API = "/api/workforce"
BACKEND = Path(__file__).resolve().parent.parent
WORKER = "import asyncio,sys;import app.worker as w;sys.exit(asyncio.run(w.main()))"  # the real engines, untouched


@pytest.fixture()
def clean_bus(monkeypatch):
    """Empty queues and outbox, and the API process in bus mode — as in production."""
    def clean():
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

    clean()
    monkeypatch.setenv("EXECUTION_DISPATCH", "bus")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    clean()


def start_worker() -> subprocess.Popen:
    env = {**os.environ, "EXECUTION_DISPATCH": "bus", "RABBITMQ_URL": TEST_AMQP, "PYTHONUNBUFFERED": "1",
           "WORKER_ROLES": "executions,orchestrator"}
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


def stop_worker(p: subprocess.Popen, hard: bool = False) -> None:
    p.kill() if hard else p.terminate()
    p.wait(timeout=20)


def wait_for(pred, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    return bool(pred())


def test_an_objective_runs_over_the_real_bus_and_survives_a_killed_worker(admin_client, db, clean_bus):
    h.reset(db)
    h.automatic(db, 1)  # W1 works on the mock provider; W2 stays Human-Assisted
    r = admin_client.post(f"{API}/objectives", json={
        "title": "Over the bus", "prompt": "1. Research part [research]\n2. Build part [engineering] (after 1)"})
    assert r.status_code == 201 and r.json()["status"] == "planning"  # the API process plans nothing itself
    oid = r.json()["id"]
    assert db.scalar(select(func.count()).select_from(Task).where(Task.workflow_id == oid)) == 0

    def objective() -> dict:
        return admin_client.get(f"{API}/objectives/{oid}").json()

    def tasks() -> dict[str, dict]:
        return {t["title"].removeprefix("[MOCK] "): t for t in objective()["tasks"]}

    def state(name: str) -> str | None:
        return tasks().get(name, {}).get("lifecycle")

    w = start_worker()
    try:
        # plan (Manager) → task 1 to W1's provider → its result reviewed → task 2 offered to a person
        assert wait_for(lambda: state("Build part") == "WAITING_FOR_ACCEPTANCE"), objective()
    finally:
        stop_worker(w, hard=True)  # the orchestrator and the executors die
    first, second = tasks()["Research part"], tasks()["Build part"]
    assert first["lifecycle"] == "COMPLETED" and first["mode"] == "AUTOMATIC" and first["attempts"] == 1
    assert second["mode"] == "HUMAN_ASSISTED" and second["worker"]["label"] == "W2"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Execution).where(Execution.task_id == first["id"])) == 1
    assert db.scalar(select(func.count()).select_from(Execution).where(Execution.task_id == second["id"])) == 0

    # a person does the second task while no worker process is alive: recorded, and it waits
    assert admin_client.post(f"{API}/tasks/{second['id']}/accept").status_code == 200
    brief = admin_client.get(f"{API}/tasks/{second['id']}").json()["brief"]["message"]
    # the person gets the same brief a model would: W1's approved result as fenced, untrusted data
    assert "UNTRUSTED DATA" in brief and "output of prerequisite task" in brief
    assert "[MOCK — not real AI]" in brief
    r = admin_client.post(f"{API}/tasks/{second['id']}/submit", json={"content": "built by hand from the research"})
    assert r.status_code == 201
    time.sleep(1.0)
    assert state("Build part") == "SUBMITTED" and objective()["status"] == "running"

    w2 = start_worker()
    try:
        assert wait_for(lambda: objective()["status"] == "completed"), objective()  # resumed from DB + outbox
    finally:
        stop_worker(w2)
    done = objective()
    assert [t["lifecycle"] for t in done["tasks"]] == ["COMPLETED", "COMPLETED"]
    assert all(t["attempts"] == 1 for t in done["tasks"])                # nothing ran twice
    db.expire_all()
    # …and nothing was sent twice
    assert db.scalar(select(func.count()).select_from(Execution).join(Task, Task.id == Execution.task_id)
                     .where(Task.workflow_id == oid)) == 1
    steps = db.scalars(select(ManagerStep).where(ManagerStep.workflow_id == oid)
                       .order_by(ManagerStep.started_at)).all()
    assert [s.kind for s in steps] == ["analyze", "plan", "review", "review", "consolidate"]  # each exactly once
    assert {s.status for s in steps} == {"ok"} and all(s.is_mock for s in steps)
    reports = db.scalars(select(WorkforceReport).where(WorkforceReport.workflow_id == oid)).all()
    assert len(reports) == 1 and reports[0].status == "complete" and reports[0].is_mock is True
    # accepted by the (mock) Manager with quotes found in the results: proposals — nothing was verified, and it says so
    assert done["report"]["version"] == 1 and done["report"]["counts"] == {
        "verified": 0, "human_approved": 0, "worker_proposal": 2, "unverified_claim": 0, "failed": 0, "incomplete": 0}
    assert done["report"]["is_mock"] is True
