"""Provider adapter contract (spec Phase 9, ADR-004). Credentials are passed in by the gateway at call time
and never logged, stored on the adapter beyond the call, or placed in prompts."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2 as httpx  # the official SDKs use httpx2; one HTTP stack for the whole gateway


@dataclass
class ChatRequest:
    """`messages` use one provider-neutral format that each adapter converts:
    {"role": "user", "content": str}
    {"role": "assistant", "content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
    {"role": "tool", "tool_call_id": str, "name": str, "content": str}
    `tools` are gateway.toolspec.ToolSpec objects (model-facing name, description, JSON Schema)."""
    model: str
    messages: list[dict[str, Any]]
    system: str = ""
    max_tokens: int = 4096
    temperature: float | None = None
    tools: list[Any] = field(default_factory=list)
    # Request-scoped hints an adapter may use and others ignore: session_id, idempotency_key, request_timeout.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GatewayEvent:
    type: str  # "delta" | "usage" | "tool_call" | "meta"
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)  # {"unknown": True} when the provider reports no usage
    call: dict[str, Any] = field(default_factory=dict)  # tool_call: {"id", "name", "arguments": dict}
    data: dict[str, Any] = field(default_factory=dict)  # meta: provider_request_id, bridge_version, mock, …


@dataclass
class DiscoveredModel:
    name: str
    display_name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None


class ProviderError(Exception):
    """Non-retryable provider failure (auth, bad request, unknown model)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ProviderUnavailable(Exception):
    """Retryable: rate limit, overload, 5xx, network. Triggers retry/backoff and fallback."""


class ProviderAdapter(Protocol):
    kind: str
    is_mock: bool

    def stream(self, req: ChatRequest) -> AsyncIterator[GatewayEvent]: ...
    async def list_models(self) -> list[DiscoveredModel]: ...
    async def test_connection(self) -> dict[str, Any]: ...


def classify_status(status: int, message: str) -> Exception:
    if status == 429 or status >= 500:
        return ProviderUnavailable(f"{status}: {message}")
    return ProviderError(message, status)


ADAPTERS: dict[str, type] = {}


def adapter_for(kind: str, *, api_key: str | None, base_url: str | None,
                http_client: httpx.AsyncClient | None = None,
                settings: dict[str, Any] | None = None) -> ProviderAdapter:
    if kind not in ADAPTERS:
        raise ProviderError(f"no adapter for provider kind '{kind}'")
    cls = ADAPTERS[kind]
    if getattr(cls, "accepts_settings", False):  # per-provider options (providers.settings)
        return cls(api_key=api_key, base_url=base_url, http_client=http_client, settings=settings or {})
    return cls(api_key=api_key, base_url=base_url, http_client=http_client)
