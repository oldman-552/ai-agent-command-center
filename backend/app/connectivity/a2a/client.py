"""A2A client for external agent services (A2A-02). A2A protocol v1.0, JSON-RPC binding.

    async with A2AClient("https://agents.example.com/research", token=...) as c:
        card = await c.get_card()
        task = await c.send_message("Summarise X", blocking=True)
        task = await c.wait(task["id"])            # poll until terminal / input-required

Hardening: only http(s) URLs; the RPC endpoint comes from the card's JSONRPC interface and must stay on the card's
host (a card cannot redirect calls — and the bearer token — to another origin); responses are size-capped and
validated; JSON-RPC errors become `A2AError(code, message)`. External agents are untrusted: their text is data.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Self
from urllib.parse import urlsplit

import httpx2 as httpx

PROTOCOL_VERSION = "1.0"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TERMINAL_STATES = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
INTERRUPTED_STATES = {"TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}


class A2AError(Exception):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"A2A error {code}: {message}")
        self.code, self.message, self.data = code, message, data


def _check_url(url: str) -> str:
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise A2AError(-32600, f"unsupported agent URL {url!r}")
    return url


class A2AClient:
    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 60.0,
                 http_client: httpx.AsyncClient | None = None):
        self.base_url = _check_url(base_url.rstrip("/"))
        self._token = token
        self._http = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._own_http = http_client is None
        self.card: dict[str, Any] | None = None
        self.rpc_url: str | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._own_http:
            await self._http.aclose()

    def _headers(self) -> dict[str, str]:
        h = {"A2A-Version": PROTOCOL_VERSION, "Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _get_json(self, method: str, url: str, **kw) -> Any:
        try:
            r = await self._http.request(method, url, headers=self._headers(), **kw)
        except httpx.TransportError as e:  # connect/read errors and timeouts: transient by nature
            raise A2AError(-32603, f"agent service unreachable ({type(e).__name__})") from e
        if len(r.content) > MAX_RESPONSE_BYTES:
            raise A2AError(-32006, "response too large")
        if r.status_code in (401, 403):
            raise A2AError(-32600, f"not authorized by the agent service (HTTP {r.status_code})")
        if r.status_code >= 400:
            raise A2AError(-32603, f"agent service returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise A2AError(-32006, "invalid JSON from agent service") from e

    # ---- discovery ----
    async def get_card(self) -> dict[str, Any]:
        card = await self._get_json("GET", f"{self.base_url}/.well-known/agent-card.json")
        if not isinstance(card, dict) or not all(k in card for k in ("name", "capabilities", "skills")):
            raise A2AError(-32006, "invalid agent card")
        rpc = next((i for i in card.get("supportedInterfaces") or []
                    if isinstance(i, dict) and i.get("protocolBinding") == "JSONRPC"), None)
        if rpc is None:
            raise A2AError(-32004, "agent offers no JSON-RPC interface")
        url = _check_url(str(rpc.get("url", "")))
        if urlsplit(url).netloc != urlsplit(self.base_url).netloc:
            raise A2AError(-32600, "agent card points to a different host; refusing to send credentials there")
        self.card, self.rpc_url = card, url
        return card

    # ---- JSON-RPC ----
    async def call(self, method: str, params: dict[str, Any]) -> Any:
        if self.rpc_url is None:
            await self.get_card()
        req_id = str(uuid.uuid4())
        body = await self._get_json("POST", self.rpc_url,
                                    json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or body.get("id") != req_id:
            raise A2AError(-32006, "invalid JSON-RPC response")
        if "error" in body:
            e = body["error"] or {}
            raise A2AError(int(e.get("code", -32603)), str(e.get("message", "error")), e.get("data"))
        return body.get("result")

    async def send_message(self, text: str, *, context_id: str | None = None, blocking: bool = True,
                           message_id: str | None = None) -> dict[str, Any]:
        """Returns the Task (or, if the agent answers directly, a Task-shaped wrapper around the Message)."""
        msg: dict[str, Any] = {"messageId": message_id or str(uuid.uuid4()), "role": "ROLE_USER",
                               "parts": [{"text": text}]}
        if context_id:
            msg["contextId"] = context_id
        result = await self.call("SendMessage", {"message": msg,
                                                 "configuration": {"returnImmediately": not blocking}})
        if isinstance(result, dict) and isinstance(result.get("task"), dict):
            return result["task"]
        if isinstance(result, dict) and isinstance(result.get("message"), dict):
            m = result["message"]
            return {"id": None, "contextId": m.get("contextId"), "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [{"artifactId": m.get("messageId"), "parts": m.get("parts", [])}]}
        raise A2AError(-32006, "SendMessage result has neither task nor message")

    async def get_task(self, task_id: str) -> dict[str, Any]:
        return await self.call("GetTask", {"id": task_id})

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        return await self.call("CancelTask", {"id": task_id})

    async def list_tasks(self, *, context_id: str | None = None, status: str | None = None,
                         page_size: int = 50, page_token: str = "") -> dict[str, Any]:
        p: dict[str, Any] = {"pageSize": page_size, "pageToken": page_token}
        if context_id:
            p["contextId"] = context_id
        if status:
            p["status"] = status
        return await self.call("ListTasks", p)

    async def wait(self, task_id: str, *, timeout: float = 300.0, interval: float = 0.5) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            task = await self.get_task(task_id)
            state = (task.get("status") or {}).get("state")
            if state in TERMINAL_STATES | INTERRUPTED_STATES or loop.time() >= deadline:
                return task
            await asyncio.sleep(interval)


def task_text(task: dict[str, Any]) -> str:
    """Concatenated text of a task's artifacts (the agent's answer)."""
    return "".join(p.get("text", "") for a in task.get("artifacts") or [] for p in a.get("parts") or []
                   if isinstance(p, dict))
