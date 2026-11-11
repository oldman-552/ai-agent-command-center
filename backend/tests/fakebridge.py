"""The FAKE Arena Local Bridge on a real local port: tests talk to it over real HTTP and real SSE.

It is not Arena and not the real bridge (see devtools/fake_arena_bridge.py). Results obtained through it are
mock/local-integration results and are labelled as such by the platform.
"""
from __future__ import annotations

import socket
import threading
import time

import uvicorn

from devtools.fake_arena_bridge import FakeBridge, create_app

KEY = "fake-bridge-key-0123456789-WXYZ"
ACCOUNTS = [f"owner{i}@accounts.example.test" for i in range(1, 5)]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeBridgeServer:
    def __init__(self, bridge: FakeBridge, host: str = "127.0.0.1"):
        self.bridge, self.host, self.port = bridge, host, free_port()
        self._server = uvicorn.Server(uvicorn.Config(create_app(bridge), host=host, port=self.port,
                                                     log_level="error", lifespan="off", access_log=False))
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> FakeBridgeServer:
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("fake bridge did not start")
            time.sleep(0.01)
        return self

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(5)

    # what the fake really received
    def completions(self) -> list[dict]:
        return [r for r in self.bridge.requests if r["path"].endswith("/chat/completions")]

    def turns(self, key: str) -> list[str]:
        return self.bridge.turns.get(key, [])


def four_account_bridge(**kw) -> FakeBridge:
    """Four fake accounts with one session each — the shape of a four-worker setup."""
    b = FakeBridge(key=KEY, heartbeat_seconds=0.2, **kw)
    for i, email in enumerate(ACCOUNTS, start=1):
        b.add_account(email, priority=i)
        b.add_session(email, model=f"fake-model-{i}", title=f"FAKE session {i}",
                      sid=f"00000000-0000-4000-8000-00000000000{i}")
    return b


def session_id(n: int) -> str:
    return f"00000000-0000-4000-8000-00000000000{n}"
