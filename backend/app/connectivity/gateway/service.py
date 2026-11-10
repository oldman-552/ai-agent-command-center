"""Multi-provider gateway (spec Phase 9, 20) and the real 'api' execution engine.

- Credentials are decrypted only here, at call time, and handed to the adapter — never to prompts or logs.
- Primary → fallback provider/model on ProviderUnavailable/ProviderError; each switch is audited.
- Every call writes a usage_record; a cost_record only when the model's prices are known (never invented).
"""
from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx2 as httpx  # the official SDKs use httpx2; one HTTP stack for the whole gateway
from sqlalchemy import select

from app.connectivity.gateway import adapters as _adapters  # noqa: F401  (registers adapters)
from app.connectivity.gateway import toolspec
from app.connectivity.gateway.base import ChatRequest, ProviderError, ProviderUnavailable, adapter_for
from app.context.assembler import system_message
from app.db import session_factory
from app.models import Agent, CostRecord, Execution, ModelInfo, Provider, Task, UsageRecord
from app.runtime import circuit
from app.runtime.contract import (
    EngineContext,
    EngineEvent,
    InputRequired,
    RetryableError,
    ToolDenied,
    register_engine,
)
from app.security.audit import log_activity
from app.security.crypto import decrypt_secret

# Tests may inject an httpx client (MockTransport) per provider kind.
HTTP_CLIENTS: dict[str, httpx.AsyncClient] = {}


def build_adapter(p: Provider):
    key = decrypt_secret(p.credential.encrypted_value) if p.credential else None
    return adapter_for(p.kind, api_key=key, base_url=p.base_url, http_client=HTTP_CLIENTS.get(p.kind))


def price_cost(m: ModelInfo | None, usage: dict[str, int]) -> tuple[Decimal | None, dict]:
    if not m or m.input_price_per_mtok is None or m.output_price_per_mtok is None:
        return None, {}
    cached = usage.get("cached_tokens", 0)
    cached_price = m.cached_input_price_per_mtok if m.cached_input_price_per_mtok is not None else m.input_price_per_mtok
    uncached_in = max(usage.get("input_tokens", 0) - cached, 0)
    cost = (Decimal(uncached_in) * m.input_price_per_mtok + Decimal(cached) * cached_price
            + Decimal(usage.get("output_tokens", 0)) * m.output_price_per_mtok) / Decimal(1_000_000)
    return cost.quantize(Decimal("0.000001")), {"input_per_mtok": str(m.input_price_per_mtok),
                                                "output_per_mtok": str(m.output_price_per_mtok),
                                                "cached_input_per_mtok": str(cached_price)}


def record_usage(db, *, provider: Provider, model: str, usage: dict[str, int], duration_ms: int,
                 agent_id=None, project_id=None, task_id=None, execution_id=None, retry_count=0) -> Decimal | None:
    rec = UsageRecord(organization_id=provider.organization_id, provider_id=provider.id, provider_kind=provider.kind,
                      model=model, agent_id=agent_id, project_id=project_id, task_id=task_id,
                      execution_id=execution_id, input_tokens=usage.get("input_tokens", 0),
                      output_tokens=usage.get("output_tokens", 0), cached_tokens=usage.get("cached_tokens", 0),
                      duration_ms=duration_ms, retry_count=retry_count, is_mock=provider.is_mock)
    db.add(rec)
    db.flush()
    m = db.scalar(select(ModelInfo).where(ModelInfo.provider_id == provider.id, ModelInfo.name == model))
    cost, pricing = price_cost(m, usage)
    if cost is not None and not provider.is_mock:
        db.add(CostRecord(organization_id=provider.organization_id, usage_record_id=rec.id, project_id=project_id,
                          agent_id=agent_id, task_id=task_id, amount_usd=cost, pricing=pricing))
    return cost


def _routes(db, agent: Agent) -> list[tuple[Provider, str]]:
    routes = []
    for pid, model in ((agent.provider_id, agent.model), (agent.fallback_provider_id, agent.fallback_model)):
        if pid and model:
            p = db.get(Provider, pid)
            if p and p.enabled and p.organization_id == agent.organization_id:
                routes.append((p, model))
    if not routes:
        raise ProviderError("agent has no enabled provider/model configured")
    return routes


MAX_TOOL_ROUNDS = 8
MAX_TOOL_RESULT_CHARS = 20_000


class ApiEngine:
    """Official-API execution engine: a chat turn through the gateway with fallback, and — when the agent has
    tools — a tool loop: the model asks, the runtime checks permissions and runs the tool, the result goes back to
    the model, until it answers without tool calls (at most `max_tool_rounds`). A checkpoint after every round
    lets a resumed execution continue the same conversation on the same model."""

    kind, is_mock = "api", False

    async def run(self, ctx: EngineContext) -> AsyncIterator[EngineEvent]:
        db = session_factory()()
        try:
            ex = db.get(Execution, ctx.execution_id)
            agent = db.get(Agent, ex.agent_id)
            if (agent.context_policy or {}).get("routing") == "auto":  # GW-07: rank models per request
                from app.connectivity.gateway import routing
                task = db.get(Task, ex.task_id) if ex.task_id else None
                need = routing.assess(agent, ctx.input, task.task_type if task else None)
                ranked, rejected = routing.rank(db, agent, need)
                decision = routing.explain(need, ranked, rejected)
                log_activity(db, "gateway.routed", category="runtime", organization_id=agent.organization_id,
                             agent_id=agent.id, execution_id=ex.id, details=decision)
                db.commit()
                top = decision["candidates"][0] if decision["candidates"] else None
                yield EngineEvent("log", {"message": f"routing: tier {need.tier} → "
                                          f"{top['provider'] + '/' + top['model'] if top else 'no model'}",
                                          "routing": decision})
                if not ranked:
                    raise ProviderError("no enabled model satisfies this request (see the routing decision)")
                routes = [(c.provider, c.model.name) for c in ranked[:3]]
            else:
                routes = _routes(db, agent)
            state = dict(ctx.resume_state or {})
            messages = list(state.get("messages") or ctx.input.get("messages")
                            or [{"role": "user", "content": str(ctx.input.get("prompt", ""))}])
            tools = toolspec.for_execution(db, ex, agent)
            by_name = {t.name: t for t in tools}
            rounds = int(state.get("tool_rounds", 0))
            max_rounds = int((agent.context_policy or {}).get("max_tool_rounds", MAX_TOOL_ROUNDS))
            if state.get("route"):  # a conversation with tool calls stays on the model that started it
                pid, model = state["route"]
                pinned = db.get(Provider, int(pid))
                routes = [(pinned, model)] if pinned is not None else routes
            while True:
                text_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                last_error: Exception | None = None
                used = None
                for i, (provider, model) in enumerate(routes):
                    if not circuit.allows(db, "provider", provider.id):  # EV-03: skip a provider whose circuit is open
                        log_activity(db, "gateway.circuit_skip", category="runtime", level="warning",
                                     organization_id=provider.organization_id, agent_id=agent.id, execution_id=ex.id,
                                     details={"provider": provider.name})
                        db.commit()
                        last_error = ProviderUnavailable(f"provider {provider.name} circuit open")
                        continue
                    if i > 0:
                        log_activity(db, "gateway.fallback", category="runtime", level="warning",
                                     organization_id=provider.organization_id, agent_id=agent.id, execution_id=ex.id,
                                     details={"to_provider": provider.name, "to_model": model,
                                              "reason": str(last_error)})
                        db.commit()
                        yield EngineEvent("log", {"message": f"fallback to {provider.name}/{model}"})
                    ex.provider, ex.model, ex.is_mock = provider.kind, model, provider.is_mock
                    db.commit()
                    req = ChatRequest(model=model, messages=messages, system=system_message(agent.system_instructions),
                                      max_tokens=int(agent.context_policy.get("max_output_tokens", 4096)),
                                      temperature=agent.context_policy.get("temperature"), tools=tools)
                    started, usage, produced = time.monotonic(), {}, False
                    text_parts, calls = [], []
                    try:
                        async for ev in build_adapter(provider).stream(req):
                            if ev.type == "delta":
                                produced = True
                                text_parts.append(ev.text)
                                yield EngineEvent("output_delta", {"text": ev.text})
                            elif ev.type == "tool_call":
                                produced = True
                                calls.append(ev.call)
                            elif ev.type == "usage":
                                usage = ev.usage
                    except (ProviderUnavailable, ProviderError) as e:
                        last_error = e
                        if isinstance(e, ProviderUnavailable):
                            circuit.record_failure(db, "provider", provider.id, provider.organization_id, str(e))
                            db.commit()
                        if produced or rounds:  # output or tool work already happened — don't splice another model in
                            if isinstance(e, ProviderUnavailable):
                                raise RetryableError(str(e)) from e
                            raise
                        continue
                    circuit.record_success(db, "provider", provider.id, provider.organization_id)
                    cost = record_usage(db, provider=provider, model=model, usage=usage,
                                        duration_ms=int((time.monotonic() - started) * 1000), agent_id=agent.id,
                                        project_id=ex.project_id, task_id=ex.task_id, execution_id=ex.id,
                                        retry_count=ex.attempt - 1)
                    if cost is not None:
                        ex.cost_usd = (ex.cost_usd or Decimal(0)) + cost
                    db.commit()
                    yield EngineEvent("usage", {**usage, "provider": provider.kind, "model": model,
                                                "cost_usd": str(cost) if cost is not None else None})
                    used = (provider, model)
                    break
                if used is None:
                    if isinstance(last_error, ProviderUnavailable):
                        raise RetryableError(str(last_error)) from last_error
                    raise last_error or ProviderError("no route succeeded")
                if not calls:
                    return
                if rounds >= max_rounds:
                    raise ProviderError(f"the model kept calling tools after {max_rounds} rounds; stopped "
                                        "(context_policy.max_tool_rounds)")
                messages.append({"role": "assistant", "content": "".join(text_parts), "tool_calls": calls})
                question = None
                for c in calls:
                    spec = by_name.get(c.get("name", ""))
                    if spec is not None and spec.key == "ask.human" and question is None:
                        # permission checked like any tool; then park — the answer becomes this call's result
                        try:
                            await ctx.call_tool(spec.key, c.get("arguments") or {})
                        except ToolDenied:
                            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": c.get("name", ""),
                                             "content": json.dumps({"error": "permission_denied"})})
                            continue
                        question = {"question": str((c.get("arguments") or {}).get("question", ""))[:4000].strip()
                                    or "(the agent asked for input without a question)",
                                    "tool_call_id": c.get("id", ""), "name": c.get("name", "")}
                        continue
                    if spec is None:  # a tool the agent was not offered: never run, answered to the model
                        result: dict[str, Any] = {"error": "unknown_tool", "reason": "this tool is not available"}
                    else:
                        try:
                            result = await ctx.call_tool(spec.key, c.get("arguments") or {})
                        except ToolDenied:
                            result = {"error": "permission_denied", "reason": f"the agent may not use {spec.key}"}
                    content = json.dumps(result, default=str, ensure_ascii=False)
                    if len(content) > MAX_TOOL_RESULT_CHARS:
                        content = content[:MAX_TOOL_RESULT_CHARS] + " …[truncated]"
                    messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "name": c.get("name", ""),
                                     "content": content})
                rounds += 1
                routes = [used]
                await ctx.checkpoint({"messages": messages, "tool_rounds": rounds, "route": [used[0].id, used[1]],
                                      **({"pending_input": question} if question else {})})
                if question:
                    raise InputRequired(question["question"])
        finally:
            db.close()


register_engine("api", ApiEngine)


async def gateway_test_connection(p: Provider) -> dict[str, Any]:
    return await build_adapter(p).test_connection()
