"""FAKE Arena Local Bridge — for tests and local demos only. It is NOT Arena and NOT the real bridge.

It reproduces, from the source of arena-local-bridge v1.1.0 (`src/server.mjs`, `src/bridge.mjs`, `src/pool.mjs`,
`src/credentials.mjs`), the behaviour the platform depends on:

  * the loopback Host/Origin guard on every route (403 "This server only answers requests from this machine.");
  * unauthenticated `/health`, `/ready`, `/api/sessions`, `/api/status`; Bearer-protected `/v1/models` and
    `/v1/chat/completions`;
  * `model` routing: session UUID (owner account is always used, 409 `session_account_unavailable`), `active`
    (409 `no_active_session`), `ping`/`health` (instant `pong`), anything else on the pool's primary account;
  * only the LAST user message reaches the "session"; system messages are ignored on the session path;
  * one turn at a time through a queue with a maximum depth (503 `bridge_queue_full`, `Retry-After: 10`), a
    per-client rate limit (429 `rate_limited`);
  * joining an identical in-flight request, replaying a finished one only with `x-arena-idempotency-key`, for a TTL;
    a turn keeps running after its client disconnected (the real bridge has no abort on close);
  * SSE: a role chunk first, empty-delta heartbeats, text deltas, a finish chunk, `[DONE]`; a failure after the stream
    opened is an in-band `data: {"error": …}` frame; no server-side timeout;
  * `usage` is always zeros; `X-Arena-Bridge-Request-Id` and `X-Arena-Bridge-Version` on completions.

It always identifies itself: `service: "fake-arena-bridge"` in `/health`, the header `X-Fake-Arena-Bridge: 1`, and
every answer starts with `[FAKE ARENA BRIDGE — not Arena]`. The platform marks a provider that talks to it as mock.

Test hooks (never present on the real bridge): markers inside a prompt —
    [[fake:error]] [[fake:error:CODE:STATUS]] [[fake:empty]] [[fake:toolcall]] [[fake:garbage]]
    [[fake:midstream-error]] [[fake:slow:SECONDS]] [[fake:drop]]
and `POST /__fake/config`, `GET /__fake/log` (Bearer key) to change its state and read what it received.

    python -m devtools.fake_arena_bridge --port 20140 --key local-dev-key
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

VERSION = "fake-1.1.0"
SERVICE = "fake-arena-bridge"
PREFIX = "[FAKE ARENA BRIDGE — not Arena] "
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
MARK = re.compile(r"\[\[fake:([a-z-]+)(?::([^\]]*))?\]\]")
CLIENT_SESSION_HEADERS = ("x-codex-session-id", "x-session-id", "x-omniroute-session")
IDEMPOTENCY_HEADER = "x-arena-idempotency-key"


class TurnError(Exception):
    def __init__(self, message: str, status: int = 502, code: str | None = None, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.code, self.retry_after = status, code, retry_after


@dataclass
class FakeBridge:
    key: str = "local-dev-key"
    accounts: list[dict[str, Any]] = field(default_factory=list)  # {email, priority, disabled, lastError}
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)  # uuid -> {email, model, title, state}
    active_session: str | None = None
    ready_accounts: set[str] = field(default_factory=set)
    max_queue: int = 8
    rate_limit_rpm: int = 100
    result_cache_ttl: float = 120.0
    heartbeat_seconds: float = 10.0
    turn_seconds: float = 0.0          # how long a turn "runs"
    chunk_chars: int = 400
    fail_next: list[dict[str, Any]] = field(default_factory=list)  # [{status, code, message}] consumed per request
    # observations
    requests: list[dict[str, Any]] = field(default_factory=list)
    turns: dict[str, list[str]] = field(default_factory=dict)       # session/agent key -> prompts really "delivered"
    account_turns: dict[str, int] = field(default_factory=dict)
    status_hits: int = 0
    peak_depth: int = 0                # most turns ever waiting or running at once
    # runtime
    stats: dict[str, Any] = field(default_factory=lambda: {"requests": 0, "completed": 0, "errors": 0,
                                                           "lastSuccessAt": None, "lastErrorAt": None,
                                                           "lastErrorType": None, "totalLatencyMs": 0})
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _queue_depth: int = 0
    _active: int = 0
    _inflight: dict[str, asyncio.Future] = field(default_factory=dict)
    _cache: dict[str, tuple[float, dict]] = field(default_factory=dict)
    _windows: dict[str, tuple[int, float]] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    # ---------------- configuration helpers used by tests ----------------
    def add_account(self, email: str, *, priority: int = 1, disabled: bool = False, ready: bool = True) -> None:
        self.accounts.append({"email": email, "priority": priority, "disabled": disabled, "lastError": None})
        if ready and not disabled:
            self.ready_accounts.add(email.lower())

    def add_session(self, email: str, *, model: str = "claude-opus-4", title: str = "", sid: str | None = None) -> str:
        sid = sid or str(uuid.uuid4())
        self.sessions[sid] = {"email": email, "model": model, "title": title or f"Session of {email}", "state": "ok"}
        return sid

    def primary(self) -> dict[str, Any] | None:
        live = sorted((a for a in self.accounts if not a["disabled"]), key=lambda a: a["priority"])
        return live[0] if live else None

    # ---------------- payloads ----------------
    def health_payload(self) -> dict[str, Any]:
        p = self.primary()
        done = self.stats["completed"]
        return {
            "ok": True, "ready": bool(p and p["email"].lower() in self.ready_accounts), "service": SERVICE,
            "version": VERSION, "uptimeSeconds": int(time.time() - self.started_at), "mode": "stateless-claude-tools",
            "sessions": len(self.sessions), "activeArenaAccounts": len(self.accounts),
            "account": {"email": p["email"], "cookieExpiry": "fake"} if p else None,
            "accounts": [{"email": a["email"], "priority": a["priority"], "updatedAt": None, "hasPassword": False,
                          "disabled": a["disabled"], "lastError": a["lastError"], "cookieExpirySeconds": 86_400,
                          "quota": None} for a in sorted(self.accounts, key=lambda a: a["priority"])],
            "refresh": {"lastLoginError": None}, "recaptcha": {},
            "queue": {"active": self._active, "depth": self._queue_depth, "maxDepth": self.max_queue},
            "browser": {"launched": False, "accounts": []},
            "stats": {**{k: v for k, v in self.stats.items() if k != "totalLatencyMs"},
                      "averageLatencyMs": round(self.stats["totalLatencyMs"] / done) if done else 0},
        }

    def session_rows(self) -> list[dict[str, Any]]:
        return [{"sessionId": sid, "model": s["model"], "title": s["title"], "url": f"https://arena.ai/agent/{sid}",
                 "email": s["email"], "collectedAt": "2026-01-01T00:00:00.000Z",
                 "prompt": "FAKE first prompt (the platform must not keep this field)", "effort": ""}
                for sid, s in self.sessions.items()]

    # ---------------- the turn ----------------
    def _answer(self, key: str, account: str, prompt: str) -> str:
        n = len(self.turns.get(key, []))
        return f"{PREFIX}turn {n} on {key} :: {prompt[:6000]}"

    async def _run_turn(self, key: str, account: str, prompt: str, on_delta) -> dict[str, Any]:
        marks = {m.group(1): m.group(2) for m in MARK.finditer(prompt)}
        if self._queue_depth >= self.max_queue:
            raise TurnError("Arena bridge queue is full; retry shortly", 503, "bridge_queue_full", 10)
        self._queue_depth += 1
        self.peak_depth = max(self.peak_depth, self._queue_depth)
        try:
            async with self._lock:  # one turn at a time, like the bridge's serialized section
                self._active += 1
                try:
                    if "error" in marks:  # refused before anything is delivered
                        code, _, status = (marks["error"] or "").partition(":")
                        raise TurnError("FAKE upstream failure requested by test marker", int(status or 502), code or None)
                    self.turns.setdefault(key, []).append(prompt)  # the prompt is now "Arena's"
                    self.account_turns[account] = self.account_turns.get(account, 0) + 1
                    delay = float(marks["slow"]) if marks.get("slow") else self.turn_seconds
                    if "drop" in marks:
                        await asyncio.sleep(3600)  # delivered, never answered
                    text = "" if "empty" in marks or "toolcall" in marks else self._answer(key, account, prompt)
                    if "midstream-error" in marks:
                        if on_delta:
                            on_delta(text[: max(1, len(text) // 2)])
                        await asyncio.sleep(min(delay, 0.2))
                        raise TurnError("FAKE failure after the stream opened")
                    if on_delta and text:
                        step = max(self.chunk_chars, 1)
                        pieces = [text[i:i + step] for i in range(0, len(text), step)]
                        for piece in pieces:
                            await asyncio.sleep(delay / len(pieces))
                            on_delta(piece)
                    else:
                        await asyncio.sleep(delay)
                    message: dict[str, Any] = {"role": "assistant", "content": text}
                    if "toolcall" in marks:
                        message = {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "call_fake", "type": "function", "function": {"name": "exec_command", "arguments": "{}"}}]}
                    return {"id": f"chatcmpl-arena-{uuid.uuid4()}", "object": "chat.completion",
                            "created": int(time.time()), "model": key,
                            "choices": [{"index": 0, "message": message,
                                         "finish_reason": "tool_calls" if "toolcall" in marks else "stop"}],
                            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
                finally:
                    self._active -= 1
        finally:
            self._queue_depth -= 1

    async def converse(self, key: str, account: str, prompt: str, idem: str, on_delta) -> dict[str, Any]:
        digest = lambda v: hashlib.sha1(v.encode()).hexdigest()
        inflight_key = f"{key}|{digest(prompt)}"
        replay_key = f"{key}|{digest(f'{idem}\n{prompt}')}" if idem else ""
        existing = self._inflight.get(inflight_key)
        if existing is not None:
            return await asyncio.shield(existing)  # identical request still running: join it, no second turn
        cached = self._cache.get(replay_key) if replay_key else None
        if cached and cached[0] > time.monotonic():
            return cached[1]  # a named retry of a finished request: replayed, no second turn
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._inflight[inflight_key] = fut
        try:
            payload = await self._run_turn(key, account, prompt, on_delta)
            if replay_key:
                self._cache[replay_key] = (time.monotonic() + self.result_cache_ttl, payload)
            fut.set_result(payload)
            return payload
        except BaseException as e:
            if not fut.done():
                fut.set_exception(e)
                fut.exception()  # mark retrieved
            raise
        finally:
            self._inflight.pop(inflight_key, None)


def _json(status: int, body: Any, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers={"X-Fake-Arena-Bridge": "1", **(headers or {})})


def _is_local(request: Request) -> bool:
    name = re.sub(r":\d+$", "", request.headers.get("host", "")).lower()
    if name not in LOCAL_HOSTS:
        return False
    origin = request.headers.get("origin")
    if not origin:
        return True
    try:
        from urllib.parse import urlsplit
        return (urlsplit(origin).hostname or "").lower() in LOCAL_HOSTS
    except ValueError:
        return False


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


def _validate(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "request body must be a JSON object"
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return "messages must be a non-empty array"
    for m in msgs:
        if not isinstance(m, dict):
            return "each message must be an object"
        if m.get("role") not in ("system", "developer", "user", "assistant", "tool"):
            return f"unsupported role: {m.get('role')}"
    if "tools" in body and not isinstance(body["tools"], list):
        return "tools must be an array"
    if "max_tokens" in body and not isinstance(body["max_tokens"], int | float):
        return "max_tokens must be a number"
    if "temperature" in body and not isinstance(body["temperature"], int | float):
        return "temperature must be a number"
    return None


def create_app(bridge: FakeBridge) -> Starlette:
    def authed(request: Request) -> bool:
        return request.headers.get("authorization", "") == f"Bearer {bridge.key}"

    def guard(handler):
        async def wrapped(request: Request) -> Response:
            entry = {"path": request.url.path, "method": request.method, "host": request.headers.get("host", ""),
                     "auth": "authorization" in request.headers,
                     "session": next((request.headers[h] for h in CLIENT_SESSION_HEADERS
                                      if request.headers.get(h)), ""),
                     "idempotency": request.headers.get(IDEMPOTENCY_HEADER, ""), "at": time.time()}
            bridge.requests.append(entry)
            request.state.log = entry
            if not _is_local(request):
                return _json(403, {"error": {"message": "This server only answers requests from this machine."}})
            return await handler(request)
        return wrapped

    async def health(request: Request) -> Response:
        payload = bridge.health_payload()
        if request.url.path == "/ready" and not payload["ready"]:
            payload["ok"] = False
            return _json(503, payload)
        return _json(200, payload)

    async def sessions(_: Request) -> Response:
        rows = bridge.session_rows()
        groups: dict[str, list] = {}
        for r in rows:
            groups.setdefault(r["model"], []).append({**r, "unresolved": False,
                                                      "state": bridge.sessions[r["sessionId"]]["state"]})
        return _json(200, {"archiveDir": "C:\\fake\\archive", "sessions": rows,
                           "groups": [{"model": m, "unresolved": False, "sessions": lst, "total": len(lst)}
                                      for m, lst in sorted(groups.items())]})

    async def status(_: Request) -> Response:
        bridge.status_hits += 1  # the platform must never call this route: it hands out the bridge key
        return _json(200, {"bridgeKey": bridge.key, "port": 0})

    async def models(request: Request) -> Response:
        if not authed(request):
            return _json(401, {"error": {"message": "Invalid bridge key"}})
        data = [{"id": sid, "object": "model", "created": 0, "owned_by": "arena-session", "name": s["model"]}
                for sid, s in bridge.sessions.items()] or [
                    {"id": "agent", "object": "model", "created": 0, "owned_by": "arena-agent"}]
        data.append({"id": "ping", "object": "model", "created": 0, "owned_by": "arena-bridge", "name": "ping"})
        return _json(200, {"object": "list", "data": data})

    async def completions(request: Request) -> Response:
        if not authed(request):
            return _json(401, {"error": {"message": "Invalid bridge key"}})
        client_key = (request.client.host if request.client else "?") + "|" + request.headers.get("authorization", "")[:24]
        count, start = bridge._windows.get(client_key, (0, time.time()))
        if time.time() - start > 60:
            count, start = 0, time.time()
        bridge._windows[client_key] = (count + 1, start)
        if count + 1 > bridge.rate_limit_rpm:
            return _json(429, {"error": {"message": "Rate limit exceeded", "type": "rate_limited"}},
                         {"Retry-After": str(max(1, int(60 - (time.time() - start))))})
        request_id = str(uuid.uuid4())
        headers = {"X-Arena-Bridge-Version": VERSION, "X-Arena-Bridge-Request-Id": request_id}
        started = time.time()
        bridge.stats["requests"] += 1

        def fail(e: TurnError) -> dict[str, Any]:
            bridge.stats["errors"] += 1
            bridge.stats["lastErrorAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            bridge.stats["lastErrorType"] = str(e.code or e.status)
            return {"error": {"message": str(e), "type": "arena_agent_error", **({"code": e.code} if e.code else {}),
                              "request_id": request_id}}

        try:
            raw = await request.body()
            if len(raw) > 5 * 1024 * 1024:
                return _json(413, {"error": {"message": "Request too large", "type": "arena_agent_error",
                                             "request_id": request_id}}, headers)
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return _json(400, {"error": {"message": "invalid JSON", "type": "arena_agent_error",
                                             "request_id": request_id}}, headers)
            problem = _validate(body)
            if problem:
                bridge.stats["errors"] += 1
                return _json(400, {"error": {"message": problem, "type": "invalid_request_error",
                                             "request_id": request_id}}, headers)
            if bridge.fail_next:
                f = bridge.fail_next.pop(0)
                raise TurnError(f.get("message", "FAKE failure"), int(f.get("status", 502)), f.get("code"),
                                f.get("retry_after"))
            model = str(body.get("model") or "active").strip()
            request.state.log["model"] = model
            client_sid = next((request.headers[h].strip() for h in CLIENT_SESSION_HEADERS
                               if request.headers.get(h, "").strip()), "")
            streaming = body.get("stream") is True
            users = [m for m in body["messages"] if m.get("role") == "user"]
            last_user = _content_text(users[-1].get("content")) if users else ""
            if model in ("ping", "health"):
                payload = {"id": f"chatcmpl-arena-ping-{int(time.time() * 1000)}", "object": "chat.completion",
                           "created": int(time.time()), "model": model,
                           "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"},
                                        "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
                if streaming:  # the real bridge opens the stream first for every model, the probe included
                    sid, now = f"chatcmpl-arena-{request_id}", int(time.time())

                    def frame(delta: dict, finish: str | None = None) -> bytes:
                        return ("data: " + json.dumps({"id": sid, "object": "chat.completion.chunk", "created": now,
                                                       "model": model, "choices": [{"index": 0, "delta": delta,
                                                                                    "finish_reason": finish}]})
                                + "\n\n").encode()

                    async def probe():
                        yield frame({"role": "assistant", "content": ""})
                        yield frame({"content": "pong"})
                        yield frame({}, "stop")
                        yield b"data: [DONE]\n\n"

                    return StreamingResponse(probe(), status_code=200, media_type="text/event-stream",
                                             headers={"Cache-Control": "no-cache, no-transform",
                                                      "X-Fake-Arena-Bridge": "1", **headers})
                return _json(200, payload, headers)
            if UUID_RE.match(model):
                target = model
            elif model == "active":
                if not bridge.active_session:
                    raise TurnError("No active session selected. Open the GUI, pick a session, then retry — or pass "
                                    "model as that session UUID directly.", 409, "no_active_session")
                target = bridge.active_session
            else:
                target = None
            if target is not None:
                sess = bridge.sessions.get(target)
                email = sess["email"] if sess else ""
                acct = next((a for a in bridge.accounts if a["email"].lower() == email.lower()), None)
                if acct is None or acct["disabled"]:
                    raise TurnError(f"Session owner {email or '(unknown)'} is unavailable: "
                                    + ("the account is disabled" if acct else "no such account in the pool"),
                                    409, "session_account_unavailable")
                if sess["state"] != "ok":
                    raise TurnError("FAKE session is not answering")
                key, account, prompt = target, email, last_user  # ONLY the last user message; system is ignored
            else:
                p = bridge.primary()
                if p is None:
                    raise TurnError("No usable Arena account")
                system = "\n".join(_content_text(m.get("content")) for m in body["messages"]
                                   if m.get("role") in ("system", "developer"))
                key = f"agent:{client_sid or 'prompt-' + hashlib.sha256(last_user[:4096].encode()).hexdigest()[:12]}"
                account, prompt = p["email"], (system + "\n" if system else "") + last_user
            if not prompt.strip():
                raise TurnError("No user message to send", 400, "empty_prompt")
            idem = request.headers.get(IDEMPOTENCY_HEADER, "").strip()
            garbage = "[[fake:garbage]]" in prompt

            if not streaming:
                payload = await bridge.converse(key, account, prompt, idem, None)
                bridge.stats["completed"] += 1
                bridge.stats["totalLatencyMs"] += int((time.time() - started) * 1000)
                bridge.stats["lastSuccessAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                if garbage:
                    return Response("this is not json", status_code=200, media_type="application/json",
                                    headers={"X-Fake-Arena-Bridge": "1", **headers})
                return _json(200, payload, headers)

            stream_id = f"chatcmpl-arena-{request_id}"
            created = int(time.time())

            def chunk(delta: dict, finish: str | None = None) -> bytes:
                return ("data: " + json.dumps({"id": stream_id, "object": "chat.completion.chunk", "created": created,
                                               "model": model or "active",
                                               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
                        + "\n\n").encode()

            async def gen():
                yield chunk({"role": "assistant", "content": ""})
                q: asyncio.Queue = asyncio.Queue()
                streamed = False
                task = asyncio.create_task(bridge.converse(key, account, prompt, idem, lambda t: q.put_nowait(t)))
                try:
                    while True:
                        getter = asyncio.create_task(q.get())
                        done, _ = await asyncio.wait({getter, task}, timeout=bridge.heartbeat_seconds,
                                                     return_when=asyncio.FIRST_COMPLETED)
                        if getter in done:
                            streamed = True
                            yield chunk({"content": getter.result()})
                            continue
                        getter.cancel()
                        if task in done:
                            while not q.empty():
                                streamed = True
                                yield chunk({"content": q.get_nowait()})
                            break
                        yield chunk({})  # heartbeat: a valid empty-delta event
                    payload = task.result()
                    message = payload["choices"][0]["message"]
                    if garbage:
                        yield b"data: {not json\n\n"
                    if not streamed:
                        if message.get("tool_calls"):
                            yield chunk({"content": message.get("content"),
                                         "tool_calls": [{"index": i, **c} for i, c in enumerate(message["tool_calls"])]})
                        elif message.get("content"):
                            yield chunk({"content": message["content"]})
                    bridge.stats["completed"] += 1
                    bridge.stats["totalLatencyMs"] += int((time.time() - started) * 1000)
                    bridge.stats["lastSuccessAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    yield chunk({}, payload["choices"][0].get("finish_reason") or "stop")
                    yield b"data: [DONE]\n\n"
                except TurnError as e:  # headers are already sent: report in-band
                    yield ("data: " + json.dumps(fail(e)) + "\n\n").encode()
                    yield b"data: [DONE]\n\n"
                finally:
                    # Like the real bridge: a client that goes away does not stop the turn. It runs to its end in
                    # the "browser", and its result is kept for a named retry (the replay cache).
                    if not task.done():
                        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

            return StreamingResponse(gen(), status_code=200, media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
                                              "X-Fake-Arena-Bridge": "1", **headers})
        except TurnError as e:
            retry = e.retry_after or (60 if e.status == 429 else 10 if e.status == 503 else 0)
            return _json(e.status, fail(e), {**({"Retry-After": str(retry)} if retry else {}), **headers})

    # ---- test hooks: not part of the real bridge ----
    async def fake_config(request: Request) -> Response:
        if not authed(request):
            return _json(401, {"error": {"message": "Invalid bridge key"}})
        body = await request.json()
        for k in ("max_queue", "rate_limit_rpm", "result_cache_ttl", "heartbeat_seconds", "turn_seconds",
                  "active_session", "chunk_chars"):
            if k in body:
                setattr(bridge, k, body[k])
        for email, patch in (body.get("accounts") or {}).items():
            for a in bridge.accounts:
                if a["email"] == email:
                    a.update({k: v for k, v in patch.items() if k in ("disabled", "priority", "lastError")})
                    if "ready" in patch:
                        (bridge.ready_accounts.add if patch["ready"] else bridge.ready_accounts.discard)(email.lower())
        for sid, patch in (body.get("sessions") or {}).items():
            if sid in bridge.sessions:
                bridge.sessions[sid].update({k: v for k, v in patch.items() if k in ("state", "email", "model")})
        if "fail_next" in body:
            bridge.fail_next = list(body["fail_next"])
        if body.get("reset_log"):
            bridge.requests.clear()
            bridge.turns.clear()
            bridge.account_turns.clear()
        return _json(200, {"ok": True})

    async def fake_log(request: Request) -> Response:
        if not authed(request):
            return _json(401, {"error": {"message": "Invalid bridge key"}})
        return _json(200, {"requests": bridge.requests[-500:], "turns": {k: len(v) for k, v in bridge.turns.items()},
                           "account_turns": bridge.account_turns, "status_hits": bridge.status_hits,
                           "sessions": {sid: s["email"] for sid, s in bridge.sessions.items()}})

    return Starlette(routes=[
        Route("/health", guard(health)), Route("/ready", guard(health)),
        Route("/api/sessions", guard(sessions)), Route("/api/status", guard(status)),
        Route("/v1/models", guard(models)), Route("/models", guard(models)),
        Route("/v1/chat/completions", guard(completions), methods=["POST"]),
        Route("/chat/completions", guard(completions), methods=["POST"]),
        Route("/__fake/config", guard(fake_config), methods=["POST"]),
        Route("/__fake/log", guard(fake_log)),
    ])


def demo_bridge(key: str, accounts: int = 4) -> FakeBridge:
    """Four fake accounts, one session each — the shape of a four-worker setup."""
    b = FakeBridge(key=key, heartbeat_seconds=2.0, turn_seconds=0.6)
    for i in range(1, accounts + 1):
        email = f"fake-account-{i}@example.test"
        b.add_account(email, priority=i)
        b.add_session(email, model=f"fake-model-{i}", title=f"FAKE session {i}",
                      sid=f"00000000-0000-4000-8000-00000000000{i}")
    return b


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser(description="FAKE Arena Local Bridge (tests/demos only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=20140)
    ap.add_argument("--key", default="local-dev-key")
    ap.add_argument("--accounts", type=int, default=4)
    a = ap.parse_args()
    print(f"FAKE Arena Local Bridge on http://{a.host}:{a.port} — not Arena, for tests only", flush=True)
    uvicorn.run(create_app(demo_bridge(a.key, a.accounts)), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
