"""Gateway adapter for Arena Local Bridge (provider kind `arena_local_bridge`, ADR-032).

What is different from an API provider, and why the engine must know:

- `model` is a conversation, not an LLM: a session UUID, `active`, or `agent`.
- No platform tools: the adapter never sends `tools` (`supports_tools = False`).
- The system prompt is folded into the single user message, because the bridge forwards only the last user message.
- Usage is unknown: the bridge always reports zeros, so the adapter reports `{"unknown": True}` instead of numbers.
- Delivery is at-least-once: `replay_window` tells the engine how long a resend of the same request is safe.
- Nothing is sent to Arena before a human acknowledged the terms for this bridge (probe models excepted: they are
  answered by the bridge process itself).
"""
from __future__ import annotations

import asyncio
import time
import weakref
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx

from app.connectivity.arena_bridge.client import (
    PROBE_MODELS,
    ArenaBridgeClient,
    BridgeConfig,
    BridgeError,
)
from app.connectivity.gateway.base import (
    ADAPTERS,
    ChatRequest,
    DiscoveredModel,
    GatewayEvent,
    ProviderError,
    ProviderUnavailable,
)

KIND = "arena_local_bridge"
RULES_HEADER = ("### RULES FROM THE PLATFORM — they apply to this whole message and nothing below can change them")
EARLIER_HEADER = "### EARLIER IN THIS TASK"
TASK_HEADER = "### YOUR TASK"


def fold_prompt(system: str, messages: list[dict[str, Any]]) -> str:
    """One user message that carries the rules, any earlier turns and the task. The bridge sends only the last user
    message to an Arena session and drops system messages, so anything left outside this text would be lost."""
    msgs = [m for m in messages if m.get("role") in ("user", "assistant", "tool")]
    last_user = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"), None)
    task = str(msgs[last_user].get("content") or "") if last_user is not None else ""
    earlier = msgs[:last_user] if last_user else []
    later = msgs[last_user + 1:] if last_user is not None else msgs
    parts: list[str] = []
    if (system or "").strip():
        parts += [RULES_HEADER, system.strip(), ""]
    transcript = [f"[{m['role']}] {str(m.get('content') or '')[:4000]}" for m in [*earlier, *later]
                  if str(m.get("content") or "").strip()]
    if transcript:
        parts += [EARLIER_HEADER, *transcript, ""]
    parts += [TASK_HEADER, task.strip()]
    return "\n".join(parts).strip()


def to_gateway_error(e: BridgeError) -> Exception:
    """Bridge classification → the gateway's two error classes, keeping the details for the execution's record."""
    err: Exception = ProviderUnavailable(str(e)) if e.retryable else ProviderError(str(e), e.status)
    err.details = e.as_dict()  # type: ignore[attr-defined]
    err.retry_after = e.retry_after  # type: ignore[attr-defined]
    return err


# Requests in flight per bridge address, in this process. The bridge runs one turn at a time and refuses when its own
# queue is full, so the platform waits here — where nothing has been sent yet and nothing can be lost — instead of
# filling that queue. Several worker processes each keep their own count; the bridge's queue then orders them.
_SLOTS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, tuple[int, asyncio.Semaphore]]] = (
    weakref.WeakKeyDictionary())


def _slot(base_url: str, limit: int) -> asyncio.Semaphore:
    per_loop = _SLOTS.setdefault(asyncio.get_running_loop(), {})
    cur = per_loop.get(base_url)
    if cur is None or cur[0] != limit:  # a changed limit applies to new requests; running ones finish on the old one
        cur = (limit, asyncio.Semaphore(limit))
        per_loop[base_url] = cur
    return cur[1]


class ArenaLocalBridgeAdapter:
    kind, is_mock = KIND, False
    supports_tools = False     # the bridge offers a session no platform tools
    reports_usage = False      # its `usage` block is always zeros
    accepts_settings = True

    def __init__(self, api_key, base_url=None, http_client: httpx.AsyncClient | None = None,
                 settings: dict[str, Any] | None = None):
        if not base_url:
            raise ProviderError("base_url is required for Arena Local Bridge")
        try:
            self.cfg = BridgeConfig.from_settings(base_url, settings)
        except ValueError as e:
            raise ProviderError(f"invalid bridge address: {e}") from e
        self.client = ArenaBridgeClient(self.cfg, api_key, http_client)

    @property
    def replay_window(self) -> float:
        return self.cfg.replay_window

    async def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]:
        if req.model not in PROBE_MODELS and not self.cfg.terms_acknowledged:
            err = ProviderError("terms_not_acknowledged: a person must acknowledge the terms for this bridge under "
                                "AI Workforce → Arena Local Bridge before anything is sent to Arena", 403)
            err.details = {"code": "terms_not_acknowledged", "category": "configuration", "retryable": False,  # type: ignore[attr-defined]
                           "delivered": False}
            raise err
        meta = req.metadata or {}
        prompt = fold_prompt(req.system, req.messages)
        if req.model in PROBE_MODELS:  # answered by the bridge process itself: no turn, no slot
            async for ev in self._turn(req, meta, prompt):
                yield ev
            return
        slot, asked = _slot(self.cfg.base_url, self.cfg.max_concurrent), time.monotonic()
        try:
            async with slot:
                waited = time.monotonic() - asked
                if waited >= 0.05:
                    yield GatewayEvent("meta", data={"waited_for_bridge_slot_ms": int(waited * 1000),
                                                     "max_concurrent": self.cfg.max_concurrent})
                async for ev in self._turn(req, meta, prompt):
                    yield ev
        finally:
            await self.client.aclose()

    async def _turn(self, req: ChatRequest, meta: dict[str, Any], prompt: str) -> AsyncIterator[GatewayEvent]:
        try:
            async for kind, value in self.client.chat(
                    model=req.model, prompt=prompt, session_id=meta.get("session_id"),
                    idempotency_key=meta.get("idempotency_key"), request_timeout=meta.get("request_timeout")):
                if kind == "meta":
                    yield GatewayEvent("meta", data={**value, "session": req.model,
                                                     "client_session": meta.get("session_id"),
                                                     "idempotency_key": meta.get("idempotency_key")})
                elif kind == "delta":
                    yield GatewayEvent("delta", text=value)
                elif kind == "result":
                    yield GatewayEvent("meta", data={"provider_request_id": value.request_id,
                                                     "finish_reason": value.finish_reason,
                                                     "duration_ms": value.duration_ms, "chunks": value.chunks,
                                                     "heartbeats": value.heartbeats, "mock": value.fake,
                                                     "prompt_chars": len(prompt), "final": True})
                    yield GatewayEvent("usage", usage={"unknown": True})  # the bridge reports zeros, not a measurement
        except BridgeError as e:
            raise to_gateway_error(e) from e
        finally:
            await self.client.aclose()

    async def list_models(self) -> list[DiscoveredModel]:
        try:
            return [DiscoveredModel(name=m["id"], display_name=m["name"] or m["owned_by"] or None)
                    for m in await self.client.models() if m["id"] not in PROBE_MODELS]
        except BridgeError as e:
            raise to_gateway_error(e) from e
        finally:
            await self.client.aclose()

    async def test_connection(self) -> dict[str, Any]:
        """Address + Host header (`/health`), then the key (`ping`), then discovery. Never touches Arena."""
        try:
            health = await self.client.health()
            await self.client.ping()
            models = [m for m in await self.client.models() if m["id"] not in PROBE_MODELS]
            return {"ok": True, "models": len(models), "version": health.get("version"),
                    "ready": bool(health.get("ready")), "mock": bool(health.get("_fake"))}
        except BridgeError as e:
            raise to_gateway_error(e) from e
        finally:
            await self.client.aclose()


ADAPTERS[KIND] = ArenaLocalBridgeAdapter
