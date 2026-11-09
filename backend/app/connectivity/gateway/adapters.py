"""Concrete adapters. Each maps its SDK/API errors onto ProviderError / ProviderUnavailable."""
from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import anthropic
import httpx2 as httpx  # the official SDKs use httpx2; one HTTP stack for the whole gateway
import openai

from app.connectivity.gateway.base import (
    ADAPTERS,
    ChatRequest,
    DiscoveredModel,
    GatewayEvent,
    ProviderError,
    ProviderUnavailable,
    classify_status,
)


class AnthropicAdapter:
    kind, is_mock = "anthropic", False

    def __init__(self, api_key, base_url=None, http_client=None):
        if not api_key:
            raise ProviderError("missing API key (a Claude.ai subscription does not include API access)")
        self.client = anthropic.AsyncAnthropic(api_key=api_key, base_url=base_url or None, http_client=http_client,
                                               max_retries=0)

    @staticmethod
    def _map(e: Exception) -> Exception:
        if isinstance(e, anthropic.APIConnectionError):
            return ProviderUnavailable(f"network: {e}")
        if isinstance(e, anthropic.APIStatusError):
            return classify_status(e.status_code, e.message)
        return e

    async def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]:
        kw: dict[str, Any] = {"model": req.model, "max_tokens": req.max_tokens, "messages": req.messages}
        if req.system:
            kw["system"] = req.system
        if req.temperature is not None:
            kw["temperature"] = req.temperature
        try:
            async with self.client.messages.stream(**kw) as s:
                async for text in s.text_stream:
                    yield GatewayEvent("delta", text=text)
                final = await s.get_final_message()
            u = final.usage
            yield GatewayEvent("usage", usage={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                                               "cached_tokens": u.cache_read_input_tokens or 0})
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            raise self._map(e) from e

    async def list_models(self) -> list[DiscoveredModel]:
        try:
            page = await self.client.models.list(limit=100)
            return [DiscoveredModel(name=m.id, display_name=getattr(m, "display_name", None)) for m in page.data]
        except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
            raise self._map(e) from e

    async def test_connection(self) -> dict[str, Any]:
        models = await self.list_models()
        return {"ok": True, "models": len(models)}


class OpenAIAdapter:
    """OpenAI and any OpenAI-compatible endpoint (base_url) — e.g. local servers, other vendors."""

    kind, is_mock = "openai", False

    def __init__(self, api_key, base_url=None, http_client=None):
        if not api_key:
            raise ProviderError("missing API key")
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url or None, http_client=http_client,
                                         max_retries=0)

    @staticmethod
    def _map(e: Exception) -> Exception:
        if isinstance(e, openai.APIConnectionError):
            return ProviderUnavailable(f"network: {e}")
        if isinstance(e, openai.APIStatusError):
            return classify_status(e.status_code, e.message)
        return e

    async def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]:
        messages = ([{"role": "system", "content": req.system}] if req.system else []) + req.messages
        kw: dict[str, Any] = {"model": req.model, "messages": messages, "stream": True,
                              "stream_options": {"include_usage": True}, "max_completion_tokens": req.max_tokens}
        if req.temperature is not None:
            kw["temperature"] = req.temperature
        try:
            stream = await self.client.chat.completions.create(**kw)
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    yield GatewayEvent("delta", text=chunk.choices[0].delta.content)
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
                    yield GatewayEvent("usage", usage={"input_tokens": u.prompt_tokens,
                                                       "output_tokens": u.completion_tokens, "cached_tokens": cached})
        except (openai.APIConnectionError, openai.APIStatusError) as e:
            raise self._map(e) from e

    async def list_models(self) -> list[DiscoveredModel]:
        try:
            page = await self.client.models.list()
            return [DiscoveredModel(name=m.id) for m in page.data]
        except (openai.APIConnectionError, openai.APIStatusError) as e:
            raise self._map(e) from e

    async def test_connection(self) -> dict[str, Any]:
        return {"ok": True, "models": len(await self.list_models())}


class OpenAICompatibleAdapter(OpenAIAdapter):
    kind = "openai_compatible"

    def __init__(self, api_key, base_url=None, http_client=None):
        if not base_url:
            raise ProviderError("base_url is required for OpenAI-compatible providers")
        super().__init__(api_key or "not-needed", base_url, http_client)


class GoogleAdapter:
    """Gemini API over REST (generativelanguage.googleapis.com) — no extra SDK dependency (ADR-004)."""

    kind, is_mock = "google", False
    DEFAULT_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key, base_url=None, http_client=None):
        if not api_key:
            raise ProviderError("missing API key")
        self.base = (base_url or self.DEFAULT_URL).rstrip("/")
        self.http = http_client or httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
        self.headers = {"x-goog-api-key": api_key, "content-type": "application/json"}

    async def _check(self, r: httpx.Response) -> None:
        if r.status_code >= 400:
            body = (await r.aread()).decode(errors="replace")[:500]
            raise classify_status(r.status_code, body)

    async def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]:
        body: dict[str, Any] = {
            "contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                         for m in req.messages],
            "generationConfig": {"maxOutputTokens": req.max_tokens}}
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        if req.temperature is not None:
            body["generationConfig"]["temperature"] = req.temperature
        usage: dict[str, int] = {}
        try:
            async with self.http.stream("POST", f"{self.base}/models/{req.model}:streamGenerateContent",
                                        params={"alt": "sse"}, headers=self.headers, json=body) as r:
                await self._check(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip())
                    for c in data.get("candidates", []):
                        for p in (c.get("content") or {}).get("parts", []):
                            if p.get("text"):
                                yield GatewayEvent("delta", text=p["text"])
                    if um := data.get("usageMetadata"):
                        usage = {"input_tokens": um.get("promptTokenCount", 0),
                                 "output_tokens": um.get("candidatesTokenCount", 0),
                                 "cached_tokens": um.get("cachedContentTokenCount", 0)}
        except httpx.TransportError as e:
            raise ProviderUnavailable(f"network: {e}") from e
        if usage:
            yield GatewayEvent("usage", usage=usage)

    async def list_models(self) -> list[DiscoveredModel]:
        try:
            r = await self.http.get(f"{self.base}/models", headers=self.headers, params={"pageSize": 1000})
        except httpx.TransportError as e:
            raise ProviderUnavailable(f"network: {e}") from e
        await self._check(r)
        return [DiscoveredModel(name=m["name"].removeprefix("models/"), display_name=m.get("displayName"),
                                context_window=m.get("inputTokenLimit"), max_output_tokens=m.get("outputTokenLimit"))
                for m in r.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])]

    async def test_connection(self) -> dict[str, Any]:
        return {"ok": True, "models": len(await self.list_models())}


PLAN_MARKER = "ACC_PLAN_JSON_V1"
_PLAN_LINE = re.compile(r"^\s*(\d+)[.)]\s*(.+?)\s*(?:\[([\w,\s-]+)\])?\s*(?:\(after\s*([\d,\s]+)\))?\s*$")


def mock_plan(prompt: str) -> str:
    """TEST ONLY. Turns numbered objective lines into a plan:  `2. Build API [backend] (after 1)`.
    Every title is prefixed [MOCK] so a mock plan can never pass for a model's plan."""
    objective = prompt.split("OBJECTIVE:", 1)[-1].split("\n\nAVAILABLE", 1)[0]
    tasks = []
    for line in objective.splitlines():
        m = _PLAN_LINE.match(line)
        if not m:
            continue
        n, title, skills, after = m.groups()
        sk = [x.strip() for x in (skills or "").split(",") if x.strip()]
        tasks.append({"key": f"t{n}", "title": f"[MOCK] {title}", "description": f"[MOCK] {title}",
                      "task_type": sk[0] if sk else "general", "required_skills": sk,
                      "acceptance_criteria": [f"[MOCK] Output addresses: {title}"],
                      "depends_on": [f"t{x.strip()}" for x in (after or "").split(",") if x.strip()], "priority": 5})
    if not tasks:
        tasks = [{"key": "t1", "title": "[MOCK] " + objective.strip()[:200], "description": objective.strip(),
                  "task_type": "general", "required_skills": [], "acceptance_criteria": ["[MOCK] Objective addressed"],
                  "depends_on": [], "priority": 5}]
    return json.dumps({"summary": "[MOCK] deterministic plan — not real AI", "tasks": tasks})


class MockAdapter:
    """TEST ONLY — deterministic, never real AI. Output is prefixed so it can never be mistaken for a model."""

    kind, is_mock = "mock", True
    PREFIX = "[MOCK — not real AI] "

    def __init__(self, api_key=None, base_url=None, http_client=None):
        pass

    async def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]:
        last = req.messages[-1]["content"] if req.messages else ""
        if PLAN_MARKER in (req.system or ""):
            text = mock_plan(last)  # deterministic plan so orchestration can be tested without a real model
            yield GatewayEvent("delta", text=text)
            yield GatewayEvent("usage", usage={"input_tokens": len(last.split()), "output_tokens": len(text.split()),
                                               "cached_tokens": 0})
            return
        if "[[mock:unavailable]]" in last:
            raise ProviderUnavailable("mock provider unavailable (requested by test marker)")
        if "[[mock:error]]" in last:
            raise ProviderError("mock provider error (requested by test marker)", 400)
        text = f"{self.PREFIX}{last}"
        for word in text.split(" "):
            yield GatewayEvent("delta", text=word + " ")
        n = len(last.split())
        yield GatewayEvent("usage", usage={"input_tokens": n, "output_tokens": n + 5, "cached_tokens": 0})

    async def list_models(self) -> list[DiscoveredModel]:
        return [DiscoveredModel(name="mock-model", display_name="Mock model (deterministic, not AI)")]

    async def test_connection(self) -> dict[str, Any]:
        return {"ok": True, "models": 1, "mock": True}


for _a in (AnthropicAdapter, OpenAIAdapter, OpenAICompatibleAdapter, GoogleAdapter, MockAdapter):
    ADAPTERS[_a.kind] = _a
