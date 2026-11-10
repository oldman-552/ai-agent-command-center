"""Model routing (GW-07, spec Phase 9 "Intelligent Model Routing", ADR-027).

An agent with `context_policy.routing = "auto"` is not pinned to one model: for each execution the gateway ranks every
enabled model of the organization's enabled, non-mock-unless-allowed providers and tries them in order.

Hard filters (a model that fails one is never used):
  availability  provider enabled, provider circuit not open, provider budget not exhausted
  capability    every capability the task needs (e.g. `tools` when the agent has tools, `vision`)
  context       context window ≥ estimated prompt tokens + requested output tokens
  user config   `context_policy.allowed_provider_ids` / `excluded_models` when set
Ranking (higher is better), each term in [0, 1]:
  complexity fit  2.0 × closeness of the model tier to the needed tier (simple → tier 1, complex → tier 3)
  cost            1.0 × (cheapest estimated cost / this model's estimated cost); unknown price scores 0.5
  latency         0.5 × from recent real usage (fastest = 1); no history scores 0.5
  reliability     1.0 × recent success ratio of the provider (no history = 0.8)
  preference      0.5 × provider priority (lower number = preferred)
Every decision is returned with its reasons so it can be shown and audited. Prices are only what was configured —
nothing is invented; a model without a price is never assumed to be free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Agent, ModelInfo, Provider, UsageRecord
from app.runtime import circuit

COMPLEX_TYPES = {"architecture", "security", "code_review", "database"}
SIMPLE_TYPES = {"documentation", "general"}
WEIGHTS = {"complexity": 2.0, "cost": 1.0, "latency": 0.5, "reliability": 1.0, "preference": 0.5}


@dataclass
class Need:
    tier: int                      # 1 simple … 3 complex
    capabilities: set[str]
    prompt_tokens: int
    output_tokens: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    provider: Provider
    model: ModelInfo
    score: float
    parts: dict[str, float]
    est_cost_usd: Decimal | None


def estimate_tokens(text: str) -> int:
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return int(ascii_chars / 4 + (len(text) - ascii_chars) / 2.5) + 1


def assess(agent: Agent, input: dict[str, Any], task_type: str | None = None) -> Need:
    """How hard is this request and what does it need?"""
    prompt = str(input.get("prompt", "")) + "".join(str(m.get("content", "")) for m in input.get("messages") or [])
    tokens = estimate_tokens(prompt)
    out = int((agent.context_policy or {}).get("max_output_tokens", 4096))
    reasons: list[str] = []
    tier = 2
    if task_type in COMPLEX_TYPES:
        tier = 3
        reasons.append(f"task type {task_type} is complex")
    elif task_type in SIMPLE_TYPES and tokens < 2_000:
        tier = 1
        reasons.append(f"task type {task_type} with a short prompt is simple")
    elif tokens > 30_000:
        tier = 3
        reasons.append("very long context")
    elif tokens < 500 and not task_type:
        tier = 1
        reasons.append("short request")
    hint = (input.get("complexity") or "").lower()
    if hint in ("low", "medium", "high"):
        tier = {"low": 1, "medium": 2, "high": 3}[hint]
        reasons.append(f"complexity hint {hint}")
    caps = set((agent.context_policy or {}).get("required_capabilities", []))
    if agent.tool_permissions:
        caps.add("tools")
    if input.get("images"):
        caps.add("vision")
    return Need(tier=tier, capabilities=caps, prompt_tokens=tokens, output_tokens=out, reasons=reasons)


def _latency(db: Session, provider_id: int, model: str) -> float | None:
    since = datetime.now(UTC) - timedelta(days=7)
    return db.scalar(select(func.avg(UsageRecord.duration_ms)).where(
        UsageRecord.provider_id == provider_id, UsageRecord.model == model, UsageRecord.ts >= since,
        UsageRecord.duration_ms.is_not(None)))


def _reliability(db: Session, provider: Provider) -> float:
    """Recent success ratio of real calls through this provider (circuit state and status as tie-breakers)."""
    from app.models import Execution
    st = circuit.state(db, "provider", provider.id)
    if st == "half_open":
        return 0.3
    since = datetime.now(UTC) - timedelta(days=7)
    rows = db.execute(select(Execution.status, func.count()).where(
        Execution.organization_id == provider.organization_id, Execution.provider == provider.kind,
        Execution.created_at >= since, Execution.status.in_(["COMPLETED", "FAILED"])).group_by(Execution.status)).all()
    counts = dict(rows)
    total = sum(counts.values())
    ratio = counts.get("COMPLETED", 0) / total if total else 0.8
    return ratio * (0.6 if provider.status == "error" else 1.0)


def _cost(m: ModelInfo, need: Need) -> Decimal | None:
    if m.input_price_per_mtok is None or m.output_price_per_mtok is None:
        return None
    return (Decimal(need.prompt_tokens) * m.input_price_per_mtok
            + Decimal(need.output_tokens) * m.output_price_per_mtok) / Decimal(1_000_000)


def rank(db: Session, agent: Agent, need: Need, *, allow_mock: bool | None = None) -> tuple[list[Candidate], list[dict[str, Any]]]:
    """Ranked usable candidates and the rejected ones with the reason."""
    from app.governance import budgets
    pol = agent.context_policy or {}
    allowed = set(pol.get("allowed_provider_ids") or [])
    excluded = set(pol.get("excluded_models") or [])
    allow_mock = bool(pol.get("allow_mock", False)) if allow_mock is None else allow_mock
    rejected: list[dict[str, Any]] = []
    pool: list[tuple[Provider, ModelInfo, Decimal | None]] = []
    providers = db.scalars(select(Provider).where(Provider.organization_id == agent.organization_id,
                                                  Provider.enabled.is_(True))).all()
    for p in providers:
        why = None
        if allowed and p.id not in allowed:
            why = "not allowed by the agent's configuration"
        elif p.is_mock and not allow_mock:
            why = "mock provider (only used when explicitly allowed)"
        elif not circuit.available(db, "provider", p.id):
            why = "provider circuit open"
        elif budgets.exceeded(db, agent.organization_id, provider_id=p.id):
            why = "provider budget exhausted"
        for m in p.models:
            r = why
            if r is None and not m.enabled:
                r = "model disabled"
            elif r is None and m.name in excluded:
                r = "model excluded by the agent's configuration"
            elif r is None and not need.capabilities <= set(m.capabilities or []):
                r = f"missing capabilities: {sorted(need.capabilities - set(m.capabilities or []))}"
            elif r is None and m.context_window and m.context_window < need.prompt_tokens + need.output_tokens:
                r = f"context window {m.context_window} too small"
            if r:
                rejected.append({"provider": p.name, "model": m.name, "reason": r})
            else:
                pool.append((p, m, _cost(m, need)))
    known = [c for _, _, c in pool if c is not None and c > 0]
    cheapest = min(known) if known else None
    lat = {(p.id, m.name): _latency(db, p.id, m.name) for p, m, _ in pool}
    fastest = min((v for v in lat.values() if v), default=None)
    prios = [p.priority for p, _, _ in pool] or [0]
    lo, hi = min(prios), max(prios)
    out = []
    for p, m, cost in pool:
        parts = {
            "complexity": 1.0 - abs((m.tier or 2) - need.tier) / 2,
            "cost": float(cheapest / cost) if cheapest and cost else (1.0 if cost == 0 and cost is not None else 0.5),
            "latency": float(fastest / lat[(p.id, m.name)]) if fastest and lat[(p.id, m.name)] else 0.5,
            "reliability": _reliability(db, p),
            "preference": 1.0 if hi == lo else 1.0 - (p.priority - lo) / (hi - lo),
        }
        score = sum(WEIGHTS[k] * v for k, v in parts.items())
        out.append(Candidate(p, m, round(score, 4), {k: round(v, 3) for k, v in parts.items()}, cost))
    out.sort(key=lambda c: (-c.score, c.provider.priority, c.model.name))
    return out, rejected


def explain(need: Need, ranked: list[Candidate], rejected: list[dict[str, Any]], limit: int = 5) -> dict[str, Any]:
    return {"need": {"tier": need.tier, "capabilities": sorted(need.capabilities), "prompt_tokens": need.prompt_tokens,
                     "reasons": need.reasons},
            "candidates": [{"provider": c.provider.name, "model": c.model.name, "score": c.score, "parts": c.parts,
                            "est_cost_usd": str(c.est_cost_usd) if c.est_cost_usd is not None else None}
                           for c in ranked[:limit]],
            "rejected": rejected[:20]}
