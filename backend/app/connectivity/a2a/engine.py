"""External agent runtime adapter (EE-03): a platform agent whose work is done by an external A2A agent service.

Configuration: the agent's provider is a Provider of kind "a2a" (auth type "external agent service", spec Phase 9)
whose base_url is the remote agent's base URL (where /.well-known/agent-card.json lives) and whose encrypted
credential is the bearer token. The runtime wraps it like any engine: deadline, cancellation, retries, audit.

Crash safety: the remote task id is checkpointed right after submission, and the A2A messageId is derived from the
execution id, so a resumed execution re-attaches to the same remote task instead of submitting a second one.
Remote output is untrusted data from an external party; it is recorded as output, never executed.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx2 as httpx

from app.connectivity.a2a.client import (
    INTERRUPTED_STATES,
    TERMINAL_STATES,
    A2AClient,
    A2AError,
    task_text,
)
from app.connectivity.gateway.base import ADAPTERS, ChatRequest, DiscoveredModel, GatewayEvent, ProviderError
from app.db import session_factory
from app.models import Agent, Execution, Provider
from app.runtime.contract import EngineContext, EngineEvent, RetryableError, register_engine
from app.security.crypto import decrypt_secret

POLL_SECONDS = 1.0
HTTP_CLIENT: httpx.AsyncClient | None = None  # tests inject a transport here


class A2AServiceAdapter:
    """Lets an external A2A service be registered and tested on the Providers page. It is not a chat model."""

    kind, is_mock = "a2a", False

    def __init__(self, *, api_key: str | None, base_url: str | None, http_client=None):
        if not base_url:
            raise ProviderError("an A2A agent service needs its base URL")
        self.client = A2AClient(base_url, token=api_key, http_client=http_client or HTTP_CLIENT)

    async def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]:
        raise ProviderError("A2A agent services run through the 'a2a' execution engine, not as a chat model")
        yield  # pragma: no cover

    async def list_models(self) -> list[DiscoveredModel]:
        return []

    async def test_connection(self) -> dict[str, Any]:
        try:
            card = await self.client.get_card()
        except A2AError as e:
            raise ProviderError(str(e)) from e
        return {"ok": True, "agent": card.get("name"), "skills": [s.get("id") for s in card.get("skills", [])],
                "streaming": bool((card.get("capabilities") or {}).get("streaming"))}


ADAPTERS["a2a"] = A2AServiceAdapter


def _client_for(execution_id: str) -> A2AClient:
    with session_factory()() as db:
        ex = db.get(Execution, execution_id)
        agent = db.get(Agent, ex.agent_id) if ex and ex.agent_id else None
        p = db.get(Provider, agent.provider_id) if agent and agent.provider_id else None
        if p is None or p.kind != "a2a" or not p.enabled:
            raise ProviderError("agent has no enabled A2A agent service configured as its provider")
        token = decrypt_secret(p.credential.encrypted_value) if p.credential else None
        return A2AClient(p.base_url or "", token=token, http_client=HTTP_CLIENT)


class A2AEngine:
    """Delegates the execution to an external A2A agent. With a streaming agent (card `capabilities.streaming`)
    the answer arrives as live output chunks over SSE; otherwise — and after a resume — the task is polled.
    State `streamed` counts characters already emitted, so a resumed run never repeats output."""
    kind, is_mock = "a2a", False

    async def run(self, ctx: EngineContext) -> AsyncIterator[EngineEvent]:
        client = _client_for(ctx.execution_id)
        state = dict(ctx.resume_state or {})
        try:
            if not state.get("remote_task_id"):
                prompt = str(ctx.input.get("prompt", ""))
                card = await client.get_card()
                if (card.get("capabilities") or {}).get("streaming"):
                    task = None
                    async for ev in self._streamed(ctx, client, prompt, state):
                        if isinstance(ev, dict):
                            task = ev
                        else:
                            yield ev
                    if task is None:  # cancelled, or the agent answered with a direct message
                        return
                else:
                    task = await client.send_message(prompt, blocking=False, message_id=f"acc-{ctx.execution_id}",
                                                     context_id=state.get("context_id"))
                    state.update(remote_task_id=task.get("id"), context_id=task.get("contextId"))
                    await ctx.checkpoint(state)
                    yield EngineEvent("log", {"message": "submitted to external A2A agent",
                                              "remote_task_id": task.get("id"), "agent": card.get("name")})
            else:
                task = await client.get_task(state["remote_task_id"])
                yield EngineEvent("log", {"message": "re-attached to external A2A task",
                                          "remote_task_id": state["remote_task_id"]})
            last = None
            while True:
                st = (task.get("status") or {}).get("state")
                if st != last:
                    yield EngineEvent("message", {"remote_state": st})
                    last = st
                if st == "TASK_STATE_COMPLETED":
                    rest = task_text(task)[int(state.get("streamed", 0)):]
                    if rest:
                        yield EngineEvent("output_delta", {"text": rest})
                    return
                if st in TERMINAL_STATES:
                    raise ProviderError(f"external agent ended the task as {st}")
                if st in INTERRUPTED_STATES:
                    raise ProviderError(f"external agent needs {st.removeprefix('TASK_STATE_').lower()}, "
                                        "which is not supported yet")
                if task.get("id") is None:  # the agent answered with a direct message
                    return
                if await ctx.is_cancelled():
                    await _cancel_remote(client, task["id"])
                    yield EngineEvent("log", {"message": "cancelled external A2A task"})  # runtime sees the flag
                    return
                await asyncio.sleep(POLL_SECONDS)
                task = await client.get_task(task["id"])
        except A2AError as e:
            if e.code == -32603:  # remote internal error / HTTP 5xx
                raise RetryableError(str(e)) from e
            raise ProviderError(str(e)) from e
        except (httpx.TransportError, httpx.TimeoutException) as e:
            raise RetryableError(f"external agent unreachable: {type(e).__name__}") from e
        finally:
            await client.aclose()

    @staticmethod
    async def _streamed(ctx: EngineContext, client: A2AClient, prompt: str, state: dict[str, Any]):
        """SendStreamingMessage. Yields EngineEvents, and finally the remote Task as last seen (a dict) for the
        common completion logic. The stream is read by a separate task so cancellation is noticed while it is idle."""
        msg: dict[str, Any] = {"messageId": f"acc-{ctx.execution_id}", "role": "ROLE_USER", "parts": [{"text": prompt}]}
        if state.get("context_id"):
            msg["contextId"] = state["context_id"]
        queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        done = object()

        async def reader():
            try:
                async for res in client.stream("SendStreamingMessage", {"message": msg}):
                    await queue.put(res)
            except BaseException as e:  # noqa: BLE001 — handed to the consumer
                await queue.put(e)
                return
            await queue.put(done)
        rd = asyncio.create_task(reader())
        task: dict[str, Any] | None = None
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), POLL_SECONDS)
                except TimeoutError:
                    if await ctx.is_cancelled():
                        if task and task.get("id"):
                            await _cancel_remote(client, task["id"])
                        yield EngineEvent("log", {"message": "cancelled external A2A task"})
                        return
                    continue
                if item is done:
                    break
                if isinstance(item, BaseException):
                    raise item
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("message"), dict):  # a direct answer instead of a task
                    text = "".join(p.get("text", "") for p in item["message"].get("parts") or [] if isinstance(p, dict))
                    if text:
                        yield EngineEvent("output_delta", {"text": text})
                    return
                if isinstance(item.get("task"), dict):
                    first = task is None
                    task = item["task"]
                    if first:
                        state.update(remote_task_id=task.get("id"), context_id=task.get("contextId"))
                        await ctx.checkpoint(state)
                        yield EngineEvent("log", {"message": "submitted to external A2A agent (streaming)",
                                                  "remote_task_id": task.get("id"),
                                                  "agent": (client.card or {}).get("name")})
                elif isinstance(item.get("artifactUpdate"), dict) and task is not None:
                    parts = (item["artifactUpdate"].get("artifact") or {}).get("parts") or []
                    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
                    if text:
                        yield EngineEvent("output_delta", {"text": text})
                        state["streamed"] = int(state.get("streamed", 0)) + len(text)
                        await ctx.checkpoint(state)
                elif isinstance(item.get("statusUpdate"), dict) and task is not None:
                    task = {**task, "status": item["statusUpdate"].get("status") or task.get("status")}
        finally:
            rd.cancel()
        if task is not None:
            st = (task.get("status") or {}).get("state")
            if st == "TASK_STATE_COMPLETED" and task.get("id"):
                task = await client.get_task(task["id"])  # authoritative artifacts; only the unstreamed rest is used
            yield task


async def _cancel_remote(client: A2AClient, task_id: str) -> None:
    try:
        await client.cancel_task(task_id)
    except A2AError:
        pass  # best effort; the platform side is cancelled regardless


register_engine("a2a", A2AEngine)
