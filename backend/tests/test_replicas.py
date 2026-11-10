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


# ---- the same two replicas behind a real nginx, configured from deploy/nginx/acc.conf ----
NGINX_CONF = BACKEND.parent / "deploy" / "nginx" / "acc.conf"


def _site_conf(api_ports: list[int], listen: int) -> str:
    """deploy/nginx/acc.conf as shipped, minus TLS and the http→https redirect, with the test's upstreams."""
    import re
    s = NGINX_CONF.read_text()
    s = re.sub(r"server \{\n    listen 80;.*?\n\}\n", "", s, count=1, flags=re.DOTALL)
    s = "\n".join(line for line in s.splitlines() if "# ACC_TLS" not in line)
    s = re.sub(r"listen 443 ssl http2;\s*# ACC_LISTEN", f"listen 127.0.0.1:{listen};", s)
    servers = "".join(f"    server 127.0.0.1:{p};\n" for p in api_ports)
    s = re.sub(r"(upstream acc_api \{\n    least_conn;\n)(?:    server [^\n]*\n)+", lambda m: m.group(1) + servers, s)
    return s


@pytest.fixture(scope="module")
def nginx(replicas, tmp_path_factory):
    import shutil
    if not shutil.which("nginx"):
        pytest.skip("nginx not installed")
    d = tmp_path_factory.mktemp("nginx")
    (d / "logs").mkdir()
    port = _port()
    api_ports = [int(u.rsplit(":", 1)[1]) for u in replicas]
    (d / "site.conf").write_text(_site_conf(api_ports, port))
    (d / "nginx.conf").write_text(f"""worker_processes 1;
error_log {d}/logs/error.log warn;
pid {d}/nginx.pid;
events {{ worker_connections 256; }}
http {{
    log_format up '$request_uri $status $upstream_addr';
    access_log {d}/logs/access.log up;
    client_body_temp_path {d}/body; proxy_temp_path {d}/proxy;
    fastcgi_temp_path {d}/fcgi; uwsgi_temp_path {d}/uwsgi; scgi_temp_path {d}/scgi;
    include {d}/site.conf;
}}
""")
    check = subprocess.run(["nginx", "-t", "-p", str(d), "-c", str(d / "nginx.conf")], capture_output=True, text=True, check=False)
    assert check.returncode == 0, check.stderr
    p = subprocess.Popen(["nginx", "-p", str(d), "-c", str(d / "nginx.conf"), "-g", "daemon off;"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while True:
        assert p.poll() is None, p.stderr.read().decode()
        try:
            if httpx.get(f"{url}/api/health", timeout=2).status_code in (200, 503):
                break
        except httpx.TransportError:
            pass
        assert time.monotonic() < deadline, "nginx did not start"
        time.sleep(0.2)
    yield url, d / "logs" / "access.log", {f"127.0.0.1:{x}" for x in api_ports}
    p.terminate()
    p.wait(timeout=10)


def test_through_nginx_requests_are_balanced_and_the_session_holds(nginx):
    url, log, upstreams = nginx
    with httpx.Client(base_url=url) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW}).status_code == 200
        for _ in range(8):
            assert c.get("/api/auth/me").json()["username"] == "admin"
    time.sleep(0.3)
    used = {line.split()[2] for line in log.read_text().splitlines() if line.startswith("/api/auth/me")}
    assert used == upstreams  # both replicas answered the same session


def test_through_nginx_sign_in_throttling_is_shared(nginx):
    url, _, _ = nginx
    login_throttle.clear()
    name = f"ghost-ngx-{uuid.uuid4().hex[:6]}"
    with httpx.Client(base_url=url) as c:
        codes = [c.post("/api/auth/login", json={"username": name, "password": "wrong-pass"}).status_code
                 for _ in range(6)]
    assert codes == [401] * 5 + [429]
    login_throttle.clear()


def test_through_nginx_websocket_upgrade_and_live_updates(nginx):
    url, log, _ = nginx

    async def go():
        async with httpx.AsyncClient(base_url=url) as c:
            assert (await c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})).status_code == 200
            cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
            csrf = c.cookies["acc_csrf"]
            async with websockets.connect(url.replace("http", "ws") + "/api/ws",
                                          additional_headers={"Cookie": cookie, "Origin": url}) as ws:
                await ws.send(json.dumps({"csrf": csrf}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 5))["topic"] == "hello"
                ids = []
                for _ in range(2):  # least_conn spreads them; the socket hears about both
                    r = await c.post("/api/projects", json={"name": f"Ngx {uuid.uuid4().hex[:6]}"},
                                     headers={"X-CSRF-Token": csrf})
                    assert r.status_code == 201, r.text
                    ids.append(r.json()["id"])
                seen: set[int] = set()
                deadline = asyncio.get_running_loop().time() + 10
                while seen != set(ids):
                    left = max(deadline - asyncio.get_running_loop().time(), 0.1)
                    m = json.loads(await asyncio.wait_for(ws.recv(), left))
                    if m.get("action") == "projects.created" and m.get("project_id") in ids:
                        seen.add(m["project_id"])
    asyncio.run(go())
    assert any(line.startswith("/api/ws 101") for line in log.read_text().splitlines())  # a real upgrade


def test_shipped_nginx_config_is_valid_with_tls(tmp_path):
    """The file exactly as shipped (TLS, redirect, http2) passes `nginx -t` once certificate paths exist."""
    import shutil
    if not (shutil.which("nginx") and shutil.which("openssl")):
        pytest.skip("nginx/openssl not installed")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=acc.test",
                    "-keyout", str(tmp_path / "privkey.pem"), "-out", str(tmp_path / "fullchain.pem")],
                   check=True, capture_output=True)
    site = NGINX_CONF.read_text().replace("/etc/ssl/acc/", f"{tmp_path}/")
    site = site.replace("listen 80;", f"listen 127.0.0.1:{_port()};").replace("listen 443 ssl", f"listen 127.0.0.1:{_port()} ssl")
    (tmp_path / "site.conf").write_text(site)
    (tmp_path / "nginx.conf").write_text(
        f"pid {tmp_path}/n.pid; error_log {tmp_path}/e.log; events {{}} http {{ access_log off; "
        f"client_body_temp_path {tmp_path}/b; proxy_temp_path {tmp_path}/p; include {tmp_path}/site.conf; }}")
    r = subprocess.run(["nginx", "-t", "-p", str(tmp_path), "-c", str(tmp_path / "nginx.conf")],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
