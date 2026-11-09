"""Provider adapter contract (spec Phase 9, ADR-004). Credentials are passed in by the gateway at call time
and never logged, stored on the adapter beyond the call, or placed in prompts."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2 as httpx  # the official SDKs use httpx2; one HTTP stack for the whole gateway


@dataclass
class ChatRequest:
    model: str
    messages: list[dict[str, str]]          # [{"role": "user"|"assistant", "content": "..."}]
    system: str = ""
    max_tokens: int = 4096
    temperature: float | None = None


@dataclass
class GatewayEvent:
    type: str  # "delta" | "usage"
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)


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
                http_client: httpx.AsyncClient | None = None) -> ProviderAdapter:
    if kind not in ADAPTERS:
        raise ProviderError(f"no adapter for provider kind '{kind}'")
    return ADAPTERS[kind](api_key=api_key, base_url=base_url, http_client=http_client)
