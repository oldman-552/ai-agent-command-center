# Context engineering

`backend/app/context/` builds what a model sees for a task (CX-01..03, ADR-024).

## Trust-separated segments

| Segment | Source | Where it goes |
|---|---|---|
| system | platform policy + the agent's configured instructions | **system message only** |
| requirements | task title, description, acceptance criteria; enabled skills assigned to the agent | user message, trusted |
| user | human review notes | user message, trusted |
| tool_output | outputs of prerequisite tasks, tool results | user message, **untrusted** block |
| retrieved | project memories | user message, **untrusted** block |

Callers cannot create system or policy segments. Untrusted content is fenced in `<<<UNTRUSTED DATA {nonce} …>>>`
blocks with a random nonce per assembly (content cannot close its own block); instruction-like text ("ignore previous
instructions", fake `<system>` tags, Arabic equivalents) is flagged inside the block and in the manifest; a policy
reminder follows all untrusted data. The API engine always sends `system_message()` — retrieved text never reaches
the system role. This mitigates prompt injection; the backend's permissions and approvals are what prevent damage.

## Budget and profiles

The budget is `context_policy.max_context_tokens` (default 8 000, estimated without a tokenizer, ~4 chars/token for
Latin and ~2.5 for other scripts). Task-type profiles (`architecture`, `frontend`, `backend`, `database`,
`security`, `testing`, `code_review`, `general`) set each segment's share and the preferred memory types; unused
share flows to the next segment in priority order (requirements → human notes → tool output → memories).

## Deduplication, prioritisation, compression

Exact duplicates are dropped everywhere; near-duplicate memories (cosine ≥ 0.92) are collapsed. Items are ranked by
score; an item that does not fit is compressed (opening + ending kept, the middle elided with a marker) before being
dropped. The **manifest** records every segment with its token estimate and trust level, and everything dropped,
compressed or flagged — shown in **Context & Memory → Context preview** and stored with the execution input.
