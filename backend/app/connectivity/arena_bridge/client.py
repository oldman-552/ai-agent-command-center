"""HTTP client for Arena Local Bridge (ADR-032).

Written against what the bridge really implements (source of v1.1.0, `src/server.mjs`), not against the OpenAI SDK:

- every request must carry a loopback `Host` header or the bridge answers 403, whatever its HOST setting;
- `/health`, `/ready` and `/api/sessions` are unauthenticated loopback routes, the `/v1/*` routes need the Bearer key;
- a streaming completion opens immediately and sends an empty-delta heartbeat every 10 s, an error after that point
  arrives as a `data:` frame, and the bridge itself never times a turn out;
- `usage` is always zeros.

The key is only ever placed in the `Authorization` header of `/v1/*` requests. `/api/status` (which returns the
bridge key) is never called. Nothing here retries: classification is returned to the caller, which owns the policy.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx2 as httpx

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}
DOCKER_HOST_NAMES = {"host.docker.internal", "gateway.docker.internal"}
HOST_GUARD_TEXT = "only answers requests from this machine"
PROBE_MODELS = {"ping", "health"}  # answered by the bridge itself: no browser, no account, no Arena request
FAKE_SERVICE = "fake-arena-bridge"
FAKE_HEADER = "x-fake-arena-bridge"
IDEMPOTENCY_HEADER = "x-arena-idempotency-key"
SESSION_HEADER = "x-codex-session-id"  # first of the bridge's CLIENT_SESSION_HEADERS

# Codes the bridge sets on its error body, and what a caller can do about them.
NEEDS_ATTENTION = {  # a retry of the same request cannot succeed; a person has to change something
    "invalid_key", "host_guard", "forbidden", "not_found", "invalid_request", "request_too_large", "empty_prompt",
    "invalid_session_id", "no_active_session", "bound_session_dead", "no_binding", "session_account_unavailable",
    "session_not_interactive", "session_not_usable", "credentials_unreadable", "terms_not_acknowledged",
    "invalid_response", "empty_response", "unexpected_tool_call", "prompt_too_long", "not_a_bridge",
    "outcome_unknown",
}
# Codes that by their meaning are raised before a message is typed into an Arena session (checked in the bridge's
# source): the request was certainly not passed on, so sending it again later is not a resend. Everything else —
# a generic failure, a broken or silent connection, a timeout — leaves the outcome unknown.
NOT_DELIVERED = {
    "unreachable", "invalid_key", "host_guard", "forbidden", "not_found", "invalid_request", "request_too_large",
    "empty_prompt", "invalid_session_id", "no_active_session", "bound_session_dead", "no_binding",
    "session_account_unavailable", "session_not_interactive", "credentials_unreadable", "rate_limited",
    "queue_full", "busy", "not_ready", "prompt_too_long", "terms_not_acknowledged",
}
ACCOUNT_CODES = {"session_account_unavailable", "session_not_usable", "credentials_unreadable", "not_ready"}
SESSION_CODES = {"no_active_session", "bound_session_dead", "no_binding", "session_not_interactive",
                 "invalid_session_id"}


class BridgeError(Exception):
    """One classified failure of a bridge call. `retryable` means: the same request may be sent again."""

    def __init__(self, code: str, message: str = "", *, status: int | None = None, retryable: bool | None = None,
                 retry_after: float | None = None, request_id: str | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = (code not in NEEDS_ATTENTION) if retryable is None else retryable
        self.retry_after = retry_after
        self.request_id = request_id
        self.in_stream = False  # reported as a frame of an open stream instead of an HTTP status

    @property
    def category(self) -> str:
        if self.code in ("unreachable", "stalled", "timeout", "connection_lost"):
            return "bridge_unavailable"
        if self.code in ACCOUNT_CODES:
            return "account_unavailable"
        if self.code in SESSION_CODES:
            return "session_unavailable"
        if self.code in ("rate_limited", "queue_full"):
            return "rate_limit"
        if self.code in ("invalid_response", "empty_response", "unexpected_tool_call", "not_a_bridge"):
            return "invalid_response"
        if self.code in ("invalid_key", "host_guard", "forbidden", "not_found", "terms_not_acknowledged"):
            return "configuration"
        return "upstream"

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message[:500], "status": self.status, "category": self.category,
                "retryable": self.retryable, "retry_after": self.retry_after, "request_id": self.request_id,
                "in_stream": self.in_stream, "delivered": False if self.code in NOT_DELIVERED else None}


def _port(u) -> int:
    return u.port or (443 if u.scheme == "https" else 80)


def normalize_base_url(url: str) -> str:
    """`http://host:20140` — without a trailing slash and without `/v1` (the adapter adds the paths)."""
    raw = str(url or "").strip()
    u = urlsplit(raw)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("invalid_url")
    if u.username or u.password or u.query or u.fragment:
        raise ValueError("invalid_url")
    path = u.path.rstrip("/")
    path = path.removesuffix("/v1")
    if path:
        raise ValueError("invalid_url")  # the bridge serves from the root; a prefix would be a different service
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"{u.scheme}://{host}:{_port(u)}"


def host_allowed(hostname: str, extra: frozenset[str] | set[str] = frozenset()) -> bool:
    """Where a bridge may live: this machine, the Docker host, or a private address. Never a public or link-local
    one — the bridge is a local, credential-bearing service, and this field must not become a way to probe a network."""
    h = hostname.lower().strip("[]").rstrip(".")
    if h in LOOPBACK_NAMES or h in DOCKER_HOST_NAMES or h in {e.lower() for e in extra}:
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    return ip.version == 4 and ip.is_private and not ip.is_link_local


def default_host_header(base_url: str) -> str:
    """The loopback authority the bridge accepts, for a base URL that is not itself loopback."""
    u = urlsplit(base_url)
    return f"127.0.0.1:{_port(u)}"


def is_loopback_url(base_url: str) -> bool:
    h = (urlsplit(base_url).hostname or "").lower()
    if h in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def mask_emails(text: str) -> str:
    """Bridge messages name accounts by e-mail address; the platform stores and shows only the masked form."""
    return _EMAIL.sub(lambda m: mask_email(m.group(0)), text or "")


def mask_email(email: str) -> str:
    """`a***@example.com` — enough to tell accounts apart on screen without storing the address."""
    e = str(email or "").strip()
    if "@" not in e:
        return (e[:1] + "***") if e else ""
    local, _, domain = e.partition("@")
    return f"{local[:1]}***@{domain}"


def account_hash(email: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{str(email or '').strip().lower()}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class BridgeConfig:
    base_url: str
    host_header: str = ""            # "" = derive: loopback authority unless the URL is already loopback
    stream: bool = True
    connect_timeout: float = 5.0
    idle_timeout: float = 45.0       # streaming: no bytes for this long = stalled (the bridge heartbeats every 10 s)
    request_timeout: float = 900.0   # one turn, queue wait included; the bridge has no timeout of its own
    replay_window: float = 120.0     # the bridge's ARENA_RESULT_CACHE_TTL_MS default
    read_session_registry: bool = True
    max_prompt_chars: int = 24_000
    max_concurrent: int = 1          # one bridge process runs one turn at a time
    terms_acknowledged: bool = False

    @classmethod
    def from_settings(cls, base_url: str, settings: dict[str, Any] | None) -> BridgeConfig:
        s = dict(settings or {})

        def num(key: str, default: float, lo: float, hi: float) -> float:
            try:
                return max(lo, min(float(s.get(key, default)), hi))
            except (TypeError, ValueError):
                return default
        return cls(
            base_url=normalize_base_url(base_url), host_header=str(s.get("host_header") or "").strip(),
            stream=bool(s.get("stream", True)), connect_timeout=num("connect_timeout_s", 5, 1, 60),
            idle_timeout=num("idle_timeout_s", 45, 15, 600), request_timeout=num("request_timeout_s", 900, 10, 7200),
            replay_window=num("replay_window_s", 120, 0, 86_400),
            read_session_registry=bool(s.get("read_session_registry", True)),
            max_prompt_chars=int(num("max_prompt_chars", 24_000, 1_000, 200_000)),
            max_concurrent=int(num("max_concurrent", 1, 1, 8)), terms_acknowledged=bool(s.get("terms_ack")))

    @property
    def effective_host_header(self) -> str | None:
        """The bridge compares the Host name with exactly `127.0.0.1`, `localhost` and `::1`. Any other way of
        addressing it — `host.docker.internal`, a private address, even `127.0.0.2` — needs the override."""
        if self.host_header:
            return self.host_header
        host = (urlsplit(self.base_url).hostname or "").lower()
        return None if host in LOOPBACK_NAMES else default_host_header(self.base_url)


@dataclass
class ChatResult:
    text: str = ""
    finish_reason: str | None = None
    request_id: str | None = None
    bridge_version: str | None = None
    fake: bool = False
    chunks: int = 0
    heartbeats: int = 0
    duration_ms: int = 0
    reasoning_chars: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _error_from_body(status: int | None, body: Any, headers: httpx.Headers | None,
                     fallback_request_id: str | None = None) -> BridgeError:
    err = body.get("error") if isinstance(body, dict) else None
    err = err if isinstance(err, dict) else {}
    message = mask_emails(str(err.get("message") or ""))[:1000]
    code = str(err.get("code") or "")
    etype = str(err.get("type") or "")
    request_id = str(err.get("request_id") or "") or fallback_request_id
    retry_after = None
    if headers is not None and headers.get("retry-after"):
        try:
            retry_after = float(headers["retry-after"])
        except ValueError:
            retry_after = None
    if status == 401:
        return BridgeError("invalid_key", "the bridge refused the key", status=status, request_id=request_id)
    if status == 403:
        guard = HOST_GUARD_TEXT in message
        return BridgeError("host_guard" if guard else "forbidden",
                           ("the bridge only answers a loopback Host header; set the Host header to 127.0.0.1:<port>"
                            if guard else message or "forbidden"), status=status, request_id=request_id)
    if status == 404:
        return BridgeError("not_found", message or "no such route on the bridge (check the base URL)", status=status)
    if status == 413:
        return BridgeError("request_too_large", message or "request body over the bridge's 5 MB limit", status=status)
    if status == 429 or etype == "rate_limited":
        return BridgeError("rate_limited", message or "bridge rate limit", status=status, retryable=True,
                           retry_after=retry_after if retry_after is not None else 60.0, request_id=request_id)
    if code == "bridge_queue_full":
        return BridgeError("queue_full", message or "the bridge queue is full", status=status, retryable=True,
                           retry_after=retry_after if retry_after is not None else 10.0, request_id=request_id)
    if code:
        return BridgeError(code, message, status=status, retry_after=retry_after, request_id=request_id)
    if status == 400 or etype == "invalid_request_error":
        return BridgeError("invalid_request", message, status=status, request_id=request_id)
    if status == 503:
        return BridgeError("not_ready", message or "the bridge is not ready", status=status, retryable=True,
                           retry_after=retry_after if retry_after is not None else 10.0, request_id=request_id)
    return BridgeError("upstream_error", message or f"bridge answered {status}", status=status, retryable=True,
                       retry_after=retry_after, request_id=request_id)


class ArenaBridgeClient:
    def __init__(self, cfg: BridgeConfig, key: str | None, http: httpx.AsyncClient | None = None):
        self.cfg = cfg
        self._key = key or ""
        self._own = http is None
        # trust_env=False: never route a request for the local bridge through a proxy from the environment
        self.http = http or httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                              timeout=httpx.Timeout(30.0, connect=cfg.connect_timeout))

    async def aclose(self) -> None:
        if self._own:
            await self.http.aclose()

    # ---------------- plumbing ----------------
    def _headers(self, *, auth: bool, extra: dict[str, str] | None = None) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        host = self.cfg.effective_host_header
        if host:
            h["Host"] = host
        if auth:
            if not self._key:
                raise BridgeError("invalid_key", "no bridge key is stored")
            h["Authorization"] = f"Bearer {self._key}"
        h.update(extra or {})
        return h

    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url}{path}"

    @staticmethod
    def _transport_error(e: Exception) -> BridgeError:
        if isinstance(e, httpx.ConnectTimeout | httpx.ConnectError):  # no connection: nothing was sent
            return BridgeError("unreachable", f"cannot connect to the bridge ({type(e).__name__})", retryable=True)
        if isinstance(e, httpx.TimeoutException):
            return BridgeError("stalled", f"the bridge stopped answering ({type(e).__name__})", retryable=True)
        # the connection broke after the request went out: the turn may or may not have run
        return BridgeError("connection_lost", f"the connection to the bridge broke ({type(e).__name__})",
                           retryable=True)

    async def _get(self, path: str, *, auth: bool, timeout: float = 10.0, ok: tuple[int, ...] = (200,)) -> tuple[int, Any, httpx.Headers]:
        try:
            r = await self.http.get(self._url(path), headers=self._headers(auth=auth),
                                    timeout=httpx.Timeout(timeout, connect=self.cfg.connect_timeout))
        except httpx.TransportError as e:
            raise self._transport_error(e) from e
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code not in ok:
            raise _error_from_body(r.status_code, body, r.headers)
        if not isinstance(body, dict):
            raise BridgeError("not_a_bridge", f"{path} did not return a JSON object", status=r.status_code)
        return r.status_code, body, r.headers

    # ---------------- unauthenticated, loopback-only routes ----------------
    async def health(self) -> dict[str, Any]:
        """`/health`: always 200 on a live bridge. Says nothing about the key."""
        _, body, headers = await self._get("/health", auth=False)
        if "ok" not in body or "version" not in body:
            raise BridgeError("not_a_bridge", "the service at this address does not look like Arena Local Bridge")
        body["_fake"] = body.get("service") == FAKE_SERVICE or bool(headers.get(FAKE_HEADER))
        return body

    async def ready(self) -> tuple[bool, dict[str, Any]]:
        """`/ready`: 200 only when an account has been verified usable."""
        status, body, _ = await self._get("/ready", auth=False, ok=(200, 503))
        return status == 200 and bool(body.get("ready")), body

    async def sessions(self) -> list[dict[str, Any]]:
        """The bridge's session registry (documented GUI route): which account owns which session. Only the fields the
        platform needs are kept; prompts and local paths in the payload are dropped here."""
        _, body, _ = await self._get("/api/sessions", auth=False)
        state: dict[str, str] = {}
        for g in body.get("groups") or []:
            for s in (g.get("sessions") or []) if isinstance(g, dict) else []:
                if isinstance(s, dict) and s.get("sessionId"):
                    state[str(s["sessionId"])] = str(s.get("state") or "")
        out = []
        for s in body.get("sessions") or []:
            if isinstance(s, dict) and s.get("sessionId"):
                sid = str(s["sessionId"])
                out.append({"session_id": sid, "model": str(s.get("model") or ""), "title": str(s.get("title") or "")[:200],
                            "email": str(s.get("email") or ""), "state": state.get(sid) or str(s.get("state") or "ok")})
        return out

    # ---------------- authenticated OpenAI-compatible routes ----------------
    async def models(self) -> list[dict[str, Any]]:
        _, body, _ = await self._get("/v1/models", auth=True)
        data = body.get("data")
        if not isinstance(data, list):
            raise BridgeError("not_a_bridge", "/v1/models returned no list")
        return [{"id": str(m["id"]), "owned_by": str(m.get("owned_by") or ""), "name": str(m.get("name") or "")[:200]}
                for m in data if isinstance(m, dict) and m.get("id")]

    async def ping(self) -> ChatResult:
        """Proves URL + Host header + key without touching Arena: the bridge answers `pong` by itself."""
        res = await self.complete(model="ping", prompt="ping", stream=False, request_timeout=15.0)
        if res.text.strip() != "pong":
            raise BridgeError("not_a_bridge", "the probe model did not answer pong")
        return res

    async def complete(self, *, model: str, prompt: str, session_id: str | None = None,
                       idempotency_key: str | None = None, stream: bool | None = None,
                       request_timeout: float | None = None) -> ChatResult:
        res = ChatResult()
        async for kind, value in self.chat(model=model, prompt=prompt, session_id=session_id,
                                           idempotency_key=idempotency_key, stream=stream,
                                           request_timeout=request_timeout):
            if kind == "result":
                res = value
        return res

    async def chat(self, *, model: str, prompt: str, session_id: str | None = None,
                   idempotency_key: str | None = None, stream: bool | None = None,
                   request_timeout: float | None = None) -> AsyncIterator[tuple[str, Any]]:
        """Yield ("meta", dict) once the bridge has answered the request line, ("delta", str) for text, and finally
        ("result", ChatResult). Raises BridgeError; never returns a partial answer as a success."""
        if len(prompt) > self.cfg.max_prompt_chars:
            raise BridgeError("prompt_too_long", f"{len(prompt)} characters; the limit for this bridge is "
                                                 f"{self.cfg.max_prompt_chars}")
        if not prompt.strip():
            raise BridgeError("empty_prompt", "nothing to send")
        streaming = self.cfg.stream if stream is None else stream
        # the bridge's cap and the caller's (a worker's own timeout): the shorter one applies
        total = min(float(request_timeout), self.cfg.request_timeout) if request_timeout else self.cfg.request_timeout
        extra = {"Content-Type": "application/json"}
        if session_id:
            extra[SESSION_HEADER] = session_id[:512]
        if idempotency_key:
            extra[IDEMPOTENCY_HEADER] = idempotency_key[:256]
        if streaming:
            extra["Accept"] = "text/event-stream"
        body = {"model": model, "stream": streaming, "messages": [{"role": "user", "content": prompt}]}
        started = time.monotonic()
        res = ChatResult()
        try:
            async with asyncio.timeout(total):
                if streaming:
                    async for item in self._stream(body, extra, res):
                        yield item
                else:
                    async for item in self._single(body, extra, res, total):
                        yield item
        except TimeoutError as e:
            raise BridgeError("timeout", f"no complete answer within {int(total)} s", retryable=False,
                              request_id=res.request_id) from e
        except httpx.TransportError as e:
            err = self._transport_error(e)
            err.request_id = res.request_id
            raise err from e
        res.duration_ms = int((time.monotonic() - started) * 1000)
        if not res.text.strip():
            raise BridgeError("empty_response", "the bridge returned no text", request_id=res.request_id)
        yield "result", res

    def _meta(self, r: httpx.Response, res: ChatResult) -> dict[str, Any]:
        res.request_id = r.headers.get("x-arena-bridge-request-id") or res.request_id
        res.bridge_version = r.headers.get("x-arena-bridge-version") or res.bridge_version
        res.fake = bool(r.headers.get(FAKE_HEADER))
        return {"provider_request_id": res.request_id, "bridge_version": res.bridge_version, "mock": res.fake}

    async def _single(self, body: dict, extra: dict[str, str], res: ChatResult, total: float) -> AsyncIterator[tuple[str, Any]]:
        r = await self.http.post(self._url("/v1/chat/completions"), headers=self._headers(auth=True, extra=extra),
                                 json=body, timeout=httpx.Timeout(total, connect=self.cfg.connect_timeout))
        try:
            data = r.json()
        except ValueError:
            data = None
        meta = self._meta(r, res)
        if r.status_code != 200:
            raise _error_from_body(r.status_code, data, r.headers, res.request_id)
        yield "meta", meta
        text, finish = _message_of(data, res.request_id)
        res.text, res.finish_reason, res.chunks = text, finish, 1
        if text:
            yield "delta", text

    async def _stream(self, body: dict, extra: dict[str, str], res: ChatResult) -> AsyncIterator[tuple[str, Any]]:
        timeout = httpx.Timeout(connect=self.cfg.connect_timeout, read=self.cfg.idle_timeout, write=30.0, pool=30.0)
        async with self.http.stream("POST", self._url("/v1/chat/completions"),
                                    headers=self._headers(auth=True, extra=extra), json=body, timeout=timeout) as r:
            meta = self._meta(r, res)
            if r.status_code != 200:
                raw = await r.aread()
                try:
                    data = json.loads(raw)
                except ValueError:
                    data = None
                raise _error_from_body(r.status_code, data, r.headers, res.request_id)
            if "text/event-stream" not in (r.headers.get("content-type") or ""):
                raise BridgeError("invalid_response", "expected an event stream", request_id=res.request_id)
            yield "meta", meta
            parts: list[str] = []
            done = False
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    done = True
                    break
                try:
                    frame = json.loads(payload)
                except ValueError as e:
                    raise BridgeError("invalid_response", "unparseable stream frame", request_id=res.request_id) from e
                if not isinstance(frame, dict):
                    raise BridgeError("invalid_response", "unexpected stream frame", request_id=res.request_id)
                if "error" in frame:  # the bridge reports a failed turn in-band once the stream is open
                    err = _error_from_body(None, frame, None, res.request_id)
                    err.in_stream = True
                    raise err
                choices = frame.get("choices")
                choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                if delta.get("tool_calls"):
                    raise BridgeError("unexpected_tool_call", "the session answered with a tool call; the platform "
                                                              "offers no tools through the bridge",
                                      request_id=res.request_id)
                if isinstance(delta.get("reasoning_content"), str):
                    res.reasoning_chars += len(delta["reasoning_content"])
                text = delta.get("content")
                if isinstance(text, str) and text:
                    parts.append(text)
                    res.chunks += 1
                    yield "delta", text
                elif not delta or delta == {"role": "assistant", "content": ""}:
                    res.heartbeats += 1
                if choice.get("finish_reason"):
                    res.finish_reason = str(choice["finish_reason"])
                    if res.finish_reason == "tool_calls":
                        raise BridgeError("unexpected_tool_call", "the session ended its turn with a tool call; the "
                                          "platform offers no tools through the bridge", request_id=res.request_id)
            if not done:
                raise BridgeError("stalled", "the stream ended without its [DONE] terminator", retryable=True,
                                  request_id=res.request_id)
            res.text = "".join(parts)


def _message_of(data: Any, request_id: str | None) -> tuple[str, str | None]:
    """Text and finish reason of a non-streamed completion; anything else is an invalid response, not a success."""
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise BridgeError("invalid_response", "no choices[0].message in the completion", request_id=request_id) from e
    if isinstance(message, dict) and message.get("tool_calls"):
        raise BridgeError("unexpected_tool_call", "the session answered with a tool call; the platform offers no tools "
                                                  "through the bridge", request_id=request_id)
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise BridgeError("invalid_response", "the completion has no text content", request_id=request_id)
    finish = choice.get("finish_reason") if isinstance(choice, dict) else None
    return text, str(finish) if finish else None
