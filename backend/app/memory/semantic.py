"""Optional semantic embeddings for memory retrieval (ADR-024 follow-up).

Off by default: retrieval then uses only the local hashed embedding (store.embed). An organization can choose an
embedding model of one of its providers with an OpenAI-compatible embeddings API — OpenAI, or a local server such as
Ollama, LM Studio or vLLM (`openai_compatible`). Enabling it sends memory text to that provider, so it needs
`settings.manage` and is audited.

- Writes never call the provider: new and changed memories get their semantic vector from a background loop
  (worker role `memory_index`, or the *Reindex* action), in batches. Until then the hashed vector is used.
- Search embeds the query synchronously with a short timeout and a small cache. After a failure the provider is
  skipped for BREAKER_S, so a slow or unreachable provider never stalls retrieval: it falls back to hashed vectors.
- A memory's semantic vector is used only when it was made by the model now configured (`semantic_model`).
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import OrderedDict
from typing import Any

import httpx2 as httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db import session_factory
from app.models import Memory, Organization, Provider
from app.security.crypto import decrypt_secret

log = logging.getLogger(__name__)

KINDS = {"openai": "https://api.openai.com/v1", "openai_compatible": None}
QUERY_TIMEOUT_S, BATCH_TIMEOUT_S = 3.0, 30.0
BREAKER_S = 60.0
BATCH = 16
MAX_INPUT_CHARS = 8000
CACHE_SIZE = 256

# tests inject an httpx transport (MockTransport)
HTTP_TRANSPORT: httpx.BaseTransport | None = None

_cache: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
_broken_until: dict[str, float] = {}
_lock = threading.Lock()


class EmbeddingError(RuntimeError):
    pass


def get_config(db: Session, organization_id: int) -> dict[str, Any] | None:
    org = db.get(Organization, organization_id)
    cfg = ((org.settings or {}).get("memory") or {}).get("embedding") if org else None
    return cfg if cfg and cfg.get("provider_id") and cfg.get("model") else None


def model_id(cfg: dict[str, Any]) -> str:
    return f"{cfg['provider_id']}:{cfg['model']}"


def set_config(db: Session, org: Organization, provider_id: int | None, model: str | None) -> dict[str, Any] | None:
    mem = dict((org.settings or {}).get("memory") or {})
    if provider_id is None:
        mem.pop("embedding", None)
        cfg = None
    else:
        p = db.get(Provider, provider_id)
        if p is None or p.organization_id != org.id:
            raise EmbeddingError("provider not found")
        if p.kind not in KINDS:
            raise EmbeddingError(f"{p.kind} providers have no OpenAI-compatible embeddings API; use an OpenAI or "
                                 "OpenAI-compatible provider")
        if not model or len(model) > 128:
            raise EmbeddingError("model is required")
        cfg = {"provider_id": p.id, "model": model}
        mem["embedding"] = cfg
    org.settings = {**(org.settings or {}), "memory": mem}
    flag_modified(org, "settings")
    return cfg


def _normalise(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _request(db: Session, cfg: dict[str, Any], texts: list[str], timeout: float) -> list[list[float]]:
    p = db.get(Provider, int(cfg["provider_id"]))
    if p is None or p.kind not in KINDS:
        raise EmbeddingError("embedding provider is missing or unsupported")
    if not p.enabled:
        raise EmbeddingError("embedding provider is disabled")
    base = (p.base_url or KINDS[p.kind] or "").rstrip("/")
    if not base:
        raise EmbeddingError("embedding provider has no base URL")
    key = decrypt_secret(p.credential.encrypted_value) if p.credential else None
    if p.kind == "openai" and not key:
        raise EmbeddingError("embedding provider has no API key")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": cfg["model"], "input": [t[:MAX_INPUT_CHARS] for t in texts]}
    try:
        with httpx.Client(timeout=timeout, transport=HTTP_TRANSPORT, follow_redirects=False) as h:
            r = h.post(f"{base}/embeddings", json=body, headers=headers)
    except httpx.HTTPError as e:
        raise EmbeddingError(f"embedding provider unreachable ({type(e).__name__})") from e
    if r.status_code != 200:
        raise EmbeddingError(f"embedding provider returned HTTP {r.status_code}")
    try:
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        vecs = [_normalise([float(x) for x in d["embedding"]]) for d in data]
    except (KeyError, TypeError, ValueError) as e:
        raise EmbeddingError("embedding provider returned an unexpected response") from e
    if len(vecs) != len(texts) or not all(vecs):
        raise EmbeddingError("embedding provider returned the wrong number of vectors")
    return vecs


def query_vector(db: Session, organization_id: int, text: str) -> tuple[str, list[float]] | None:
    """(model id, vector) for a search query, or None → use the hashed embedding."""
    cfg = get_config(db, organization_id)
    if cfg is None or not text.strip():
        return None
    mid = model_id(cfg)
    with _lock:
        if _broken_until.get(mid, 0) > time.monotonic():
            return None
        hit = _cache.get((mid, text))
        if hit is not None:
            _cache.move_to_end((mid, text))
            return mid, hit
    try:
        vec = _request(db, cfg, [text], QUERY_TIMEOUT_S)[0]
    except EmbeddingError as e:
        with _lock:
            _broken_until[mid] = time.monotonic() + BREAKER_S
        log.warning("semantic query embedding failed (%s); using hashed embeddings for %ss", e, BREAKER_S)
        return None
    with _lock:
        _cache[(mid, text)] = vec
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return mid, vec


def reset() -> None:
    """Tests only."""
    with _lock:
        _cache.clear()
        _broken_until.clear()


def backfill(organization_id: int | None = None, limit: int = 200) -> dict[str, int]:
    """Give current memories a vector from the configured model. Returns counts per outcome."""
    done = failed = 0
    with session_factory()() as db:
        orgs = [organization_id] if organization_id is not None else list(db.scalars(select(Organization.id)))
        for oid in orgs:
            cfg = get_config(db, oid)
            if cfg is None:
                continue
            mid = model_id(cfg)
            rows = db.scalars(select(Memory).where(
                Memory.organization_id == oid, Memory.is_current.is_(True),
                or_(Memory.semantic_model.is_(None), Memory.semantic_model != mid))
                .order_by(Memory.id).limit(limit)).all()
            for i in range(0, len(rows), BATCH):
                chunk = rows[i:i + BATCH]
                try:
                    vecs = _request(db, cfg, [f"{m.title}\n{m.content}" for m in chunk], BATCH_TIMEOUT_S)
                except EmbeddingError as e:
                    log.warning("memory reindex for organization %s stopped: %s", oid, e)
                    failed += len(rows) - i
                    break
                for m, v in zip(chunk, vecs, strict=True):
                    m.semantic_embedding, m.semantic_model = v, mid
                done += len(chunk)
                db.commit()
    return {"indexed": done, "failed": failed}


def pending(db: Session, organization_id: int) -> int:
    cfg = get_config(db, organization_id)
    if cfg is None:
        return 0
    mid = model_id(cfg)
    return len(db.scalars(select(Memory.id).where(
        Memory.organization_id == organization_id, Memory.is_current.is_(True),
        or_(Memory.semantic_model.is_(None), Memory.semantic_model != mid))).all())


async def run(stop: asyncio.Event, interval: float = 10.0) -> None:
    """Worker loop (role `memory_index`)."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(backfill)
        except Exception:
            log.exception("memory index loop failed")
        try:
            await asyncio.wait_for(stop.wait(), interval)
        except TimeoutError:
            pass
