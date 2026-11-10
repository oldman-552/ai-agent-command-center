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
small boost, suggestions a small penalty. The embedding function can be swapped for a real model without a schema
change.
