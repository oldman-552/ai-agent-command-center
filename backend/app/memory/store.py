"""Scoped agent memory (MEM-01..03, spec Phase 8, ADR-024).

- Nine memory types; each type has a required scope (project, agent or team) — never "global by accident".
- Provenance on every row (`source`: kind user|agent|platform, refs). Only a human can mark a memory *verified*;
  anything an agent writes is stored as a suggestion/assumption, never as verified knowledge.
- Corrections create a new version that supersedes the old one (history kept); deletion removes all versions.
- Retrieval is hybrid: PostgreSQL full-text rank + cosine similarity of a local, deterministic hashed character/word
  n-gram embedding (works for Arabic and English without an external model). Results never cross projects.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Memory
from app.security.audit import log_activity
from app.security.crypto import redact

DIM = 256
PROJECT_SCOPED = {"project", "task", "technical_decision", "codebase", "execution", "lesson"}
TYPES = PROJECT_SCOPED | {"agent_identity", "conversation", "team_shared"}


def embed(text: str) -> list[float]:
    """Hashed bag of words + character trigrams, L2-normalised. Deterministic and language-agnostic."""
    v = [0.0] * DIM
    t = text.lower()
    feats = re.findall(r"\w+", t)
    for w in re.findall(r"\w{3,}", t):
        feats += [w[i:i + 3] for i in range(len(w) - 2)]
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=4).digest(), "big")
        v[h % DIM] += 1.0 if h & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float] | None, b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False)) if a else 0.0


def _check_scope(memory_type: str, project_id: int | None, agent_id: int | None, team_id: int | None) -> None:
    if memory_type not in TYPES:
        raise HTTPException(422, "invalid_memory_type")
    if memory_type in PROJECT_SCOPED and project_id is None:
        raise HTTPException(422, "project_scope_required")
    if memory_type == "agent_identity" and agent_id is None:
        raise HTTPException(422, "agent_scope_required")
    if memory_type == "team_shared" and team_id is None:
        raise HTTPException(422, "team_scope_required")


def create(db: Session, *, organization_id: int, memory_type: str, title: str, content: str, source: dict[str, Any],
           verification: str = "assumption", project_id: int | None = None, agent_id: int | None = None,
           team_id: int | None = None, task_id: int | None = None, expires_at: datetime | None = None) -> Memory:
    _check_scope(memory_type, project_id, agent_id, team_id)
    if source.get("kind") != "user" and verification == "verified":
        verification = "assumption"  # agents and automation cannot verify their own claims
    if verification not in ("verified", "assumption", "suggestion"):
        raise HTTPException(422, "invalid_verification")
    title, content = redact(title)[:255], redact(content)[:20_000]
    m = Memory(organization_id=organization_id, memory_type=memory_type, title=title, content=content,
               source=source, verification=verification, project_id=project_id, agent_id=agent_id, team_id=team_id,
               task_id=task_id, expires_at=expires_at, embedding=embed(f"{title}\n{content}"))
    db.add(m)
    db.flush()
    log_activity(db, "memory.created", category="memory", organization_id=organization_id, project_id=project_id,
                 agent_id=agent_id, actor=f"{source.get('kind')}:{source.get('ref', '')}",
                 details={"memory_id": m.id, "type": memory_type, "verification": verification})
    return m


def correct(db: Session, m: Memory, *, content: str, title: str | None, user_id: int,
            verification: str | None = None) -> Memory:
    """Human correction: a new version supersedes the old one (which stays as history)."""
    if not m.is_current:
        raise HTTPException(409, "not_current_version")
    new = Memory(organization_id=m.organization_id, memory_type=m.memory_type, title=redact(title or m.title)[:255],
                 content=redact(content)[:20_000], source={"kind": "user", "ref": f"user:{user_id}",
                                                            "corrects": m.id, "original": m.source},
                 verification=verification or "verified", project_id=m.project_id, agent_id=m.agent_id,
                 team_id=m.team_id, task_id=m.task_id, expires_at=m.expires_at, version=m.version + 1,
                 supersedes_id=m.id)
    new.embedding = embed(f"{new.title}\n{new.content}")
    new.semantic_embedding, new.semantic_model = None, None  # re-embedded by the index loop
    m.is_current = False
    db.add(new)
    db.flush()
    log_activity(db, "memory.corrected", category="memory", organization_id=m.organization_id, user_id=user_id,
                 project_id=m.project_id, details={"memory_id": new.id, "supersedes": m.id, "version": new.version})
    return new


def verify(db: Session, m: Memory, user_id: int) -> Memory:
    m.verification = "verified"
    m.source = {**m.source, "verified_by": f"user:{user_id}", "verified_at": datetime.now(UTC).isoformat()}
    log_activity(db, "memory.verified", category="memory", organization_id=m.organization_id, user_id=user_id,
                 project_id=m.project_id, details={"memory_id": m.id})
    return m


def history(db: Session, m: Memory) -> list[Memory]:
    chain, cur = [], m
    while cur is not None:
        chain.append(cur)
        cur = db.get(Memory, cur.supersedes_id) if cur.supersedes_id else None
    return chain


def delete(db: Session, m: Memory, user_id: int) -> int:
    """Delete the memory and every earlier version of it."""
    chain = history(db, m)
    for x in chain:
        db.delete(x)
    log_activity(db, "memory.deleted", category="memory", organization_id=m.organization_id, user_id=user_id,
                 project_id=m.project_id, details={"memory_ids": [x.id for x in chain]})
    return len(chain)


@dataclass
class Hit:
    memory: Memory
    score: float
    lexical: float
    semantic: float
    method: str = "hashed"  # which vector the semantic score came from: "hashed" or "model"


def search(db: Session, *, organization_id: int, query: str, project_id: int | None, agent_id: int | None = None,
           team_id: int | None = None, types: list[str] | None = None, limit: int = 10,
           include_unverified: bool = True) -> list[Hit]:
    """Current, unexpired memories visible to this scope, ranked. Never returns another project's memories."""
    now = datetime.now(UTC)
    visible = [Memory.project_id == project_id] if project_id is not None else []
    if agent_id is not None:
        visible.append((Memory.project_id.is_(None)) & (Memory.agent_id == agent_id))
    if team_id is not None:
        visible.append((Memory.project_id.is_(None)) & (Memory.team_id == team_id))
    if not visible:
        return []
    stmt = select(Memory).where(Memory.organization_id == organization_id, Memory.is_current.is_(True),
                                or_(Memory.expires_at.is_(None), Memory.expires_at > now), or_(*visible))
    if project_id is not None:  # belt and braces: a project query can never see other projects' rows
        stmt = stmt.where(or_(Memory.project_id.is_(None), Memory.project_id == project_id))
    if types:
        stmt = stmt.where(Memory.memory_type.in_(types))
    if not include_unverified:
        stmt = stmt.where(Memory.verification == "verified")
    tsq = func.plainto_tsquery("simple", query or "")
    rows = db.execute(stmt.add_columns(func.ts_rank(Memory.tsv, tsq)).limit(500)).all()
    q = embed(query or "")
    from app.memory import semantic
    qs = semantic.query_vector(db, organization_id, query or "") if rows else None
    max_lex = max((float(r[1]) for r in rows), default=0.0) or 1.0
    hits = []
    for m, lex in rows:
        if qs and m.semantic_model == qs[0] and m.semantic_embedding:
            sem, method = cosine(m.semantic_embedding, qs[1]), "model"
        else:
            sem, method = cosine(m.embedding, q), "hashed"
        boost = 0.1 if m.verification == "verified" else (-0.05 if m.verification == "suggestion" else 0.0)
        hits.append(Hit(m, 0.6 * sem + 0.4 * float(lex) / max_lex + boost, float(lex), sem, method))
    hits.sort(key=lambda h: (-h.score, -h.memory.id))
    return hits[:limit]
