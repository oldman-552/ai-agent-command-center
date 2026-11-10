"""Several API replicas behind one load balancer (DEPLOYMENT: horizontal scaling): two real uvicorn processes share
PostgreSQL and Redis. A session from one works on the other, sign-in throttling is counted across both, and a change
made through one reaches a live WebSocket held by the other."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx2 as httpx
import pytest
import websockets

from app.security.auth import login_throttle
from tests.conftest import ADMIN_PW

BACKEND = Path(__file__).resolve().parent.parent


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def replicas(_schema):
    procs, urls = [], []
    env = {**os.environ, "LOG_FORMAT": "text"}
    for _ in range(2):
        port = _port()
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port),
             "--log-level", "warning"], cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        urls.append(f"http://127.0.0.1:{port}")
    deadline = time.monotonic() + 60
    for url, p in zip(urls, procs, strict=True):
        while True:
            assert p.poll() is None, p.stderr.read().decode()[-2000:]
            try:
                if httpx.get(f"{url}/api/health", timeout=2).status_code in (200, 503):
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, "replica did not start"
            time.sleep(0.3)
    yield urls
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()


def test_session_from_one_replica_works_on_the_other(replicas):
    a, b = replicas
    with httpx.Client(base_url=a) as ca:
        assert ca.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 200
        cookies = dict(ca.cookies)
    with httpx.Client(base_url=b, cookies=cookies) as cb:
        assert cb.get("/api/auth/me").json()["username"] == "admin"


def test_sign_in_throttling_is_shared_by_all_replicas(replicas):
    login_throttle.clear()
    name = f"ghost-{uuid.uuid4().hex[:6]}"
    codes = []
    for i in range(6):  # alternate replicas: a per-process counter would never reach the limit of 5
        with httpx.Client(base_url=replicas[i % 2]) as c:
            codes.append(c.post("/api/auth/login", json={"username": name, "password": "wrong-pass"}).status_code)
    assert codes == [401] * 5 + [429]
    login_throttle.clear()


def test_change_through_one_replica_reaches_live_socket_on_the_other(replicas):
    a, b = replicas

    async def go():
        async with httpx.AsyncClient(base_url=a) as ca:
            assert (await ca.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})).status_code == 200
            cookie = "; ".join(f"{k}={v}" for k, v in ca.cookies.items())
            csrf = ca.cookies["acc_csrf"]
            async with websockets.connect(b.replace("http", "ws") + "/api/ws",
                                          additional_headers={"Cookie": cookie, "Origin": b}) as ws:
                await ws.send(json.dumps({"csrf": csrf}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 5))["topic"] == "hello"
                r = await ca.post("/api/projects", json={"name": f"Replica {uuid.uuid4().hex[:6]}"},
                                  headers={"X-CSRF-Token": csrf})
                assert r.status_code == 201, r.text
                deadline = asyncio.get_running_loop().time() + 10
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), max(deadline - asyncio.get_running_loop().time(), 0.1)))
                    if msg.get("action") == "projects.created" and msg.get("project_id") == r.json()["id"]:
                        return
    asyncio.run(go())
