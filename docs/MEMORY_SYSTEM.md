# Memory system

`backend/app/memory/` (MEM-01..04, ADR-024).

## Scope and types

Nine types: `project`, `task`, `technical_decision`, `codebase`, `execution`, `lesson` (all need a project),
`agent_identity` (needs an agent), `team_shared` (needs a team), `conversation`. Memories never cross organizations,
and a project query only sees that project's memories (plus agent/team memories without a project).

## Provenance and verification

Every memory records its source (`user`, `agent` with the execution, `platform`) and is `verified`, `assumption` or
`suggestion`. **Only a person can verify**: anything an agent writes (`memory.write` tool) is stored as a suggestion
with the execution as provenance. Text is secret-redacted before storage. Memories can expire (`expires_at`).

## Corrections and deletion

A correction creates a new, verified version that supersedes the old one (`supersedes_id`, the old row stays as
history and is no longer retrieved). Deleting removes the memory and all its earlier versions. Every create, correct,
verify and delete is audited. All of this is available on the **Context & Memory** page.

## Retrieval

Hybrid and fully local: PostgreSQL full-text rank (`tsv`, GIN index) + cosine similarity of a deterministic 256-d
hashed word/character-trigram embedding (works for Arabic and English, no external model). Verified memories get a
small boost, suggestions a small penalty.

**Optional embedding model** (Memory → *Search mode*, needs `settings.manage`; API `GET/PUT /api/memory/embedding`,
`POST /api/memory/embedding/reindex`). An organization can add the embedding model of an OpenAI or OpenAI-compatible
provider — a local Ollama, LM Studio or vLLM server keeps everything on your machine — so memories with the same
meaning but different words are found. **Enabling it sends the text of every memory to that provider** (the page says
so before you save); it is off by default and audited.
- The hashed embedding stays on every memory; the model's vector is stored next to it (`semantic_embedding`,
  `semantic_model`) and used only when it was made by the model now configured. Changing the model makes all vectors
  stale; correcting a memory clears its vector.
- Writes never call the provider: the worker (role `memory_index`) indexes new or stale memories in batches of 16;
  *Index now* does up to 200 at once. Until indexed, a memory is matched with the hashed vector.
- Search embeds the query (3 s timeout, cache of 256 queries). If the provider fails, retrieval falls back to the
  hashed vectors and the provider is skipped for 60 s, so a slow provider never stalls context assembly. Search
  results say which vector matched (`match`: `model` or `hashed`).
- Verified with a simulated embedding provider (`tests/test_memory_semantic.py`: a paraphrase found only with the
  model, invalidation, fallback and breaker, permissions, audit) and the page in E2E; not with a real model here.
