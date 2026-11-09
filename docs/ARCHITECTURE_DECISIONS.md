# Architecture Decision Records

Format: context → decision → consequences. Status is Accepted unless noted. Newest last.

## ADR-001 — Modular monolith with plane-aligned packages (2026-11-09)
**Context:** Spec v4 §1 asks for six planes with explicit service boundaries, starting as a modular monolith.
**Decision:** One FastAPI codebase (`backend/app`) with packages per plane: `control/` (agents, tasks, projects, approvals),
`runtime/` (data plane: execution contract, engines, sandbox), `connectivity/` (providers, GitHub, MCP, A2A, browser bridge),
`intelligence/` (context, memory, evaluation, routing), `security/` (auth, RBAC, policies, audit), `observability/`.
Planes talk through Python interfaces and RabbitMQ events, never by importing each other's internals.
Separate processes from the same image: `api`, `worker` (runtime executions), `orchestrator` (LangGraph), `supervisor`.
**Consequences:** One deploy unit, cheap to run locally; any plane can later be extracted behind its interface.

## ADR-002 — Runtime contract is independent of LangGraph and providers (2026-11-09)
**Decision:** `runtime/contract.py` defines `ExecutionEngine` (start/stream/cancel/checkpoint/resume) and the 13-state
machine. LangGraph is used only inside `orchestrator` to decide *what* runs; executions are dispatched to engines via
RabbitMQ. No provider SDK is imported from `runtime/`.
**Consequences:** API, CLI, browser bridge and A2A engines are interchangeable; orchestration can change without touching execution.

## ADR-003 — RabbitMQ for the internal bus, Redis for ephemeral coordination (2026-11-09)
**Decision:** RabbitMQ carries durable work and domain events (ack, DLQ, retry, priority). Redis holds ephemeral state:
distributed locks, rate-limit counters, WebSocket fan-out (pub/sub), short caches. PostgreSQL is the source of truth.
**Consequences:** Losing Redis loses no business data.

## ADR-004 — Own provider adapters over native SDKs; LiteLLM deferred (2026-11-09)
**Context:** Spec suggests LiteLLM "where appropriate". We need exact usage fields (cache tokens), streaming, and strict
credential handling.
**Decision:** Thin adapters over the official SDKs (`anthropic`, `openai`, `google-genai`) behind `ProviderAdapter`.
LiteLLM is not adopted now (large dependency surface, duplicate abstraction); revisit if many long-tail providers are needed —
an `OpenAICompatibleAdapter` already covers most of them.
**Consequences:** Fewer dependencies; adding a provider = one adapter class.

## ADR-005 — Browser Bridge disabled by default, per-provider terms gate (2026-11-09, owner-approved)
**Context:** Spec v4 Phase 11 asks for automation of consumer AI web sessions. Most providers' consumer terms prohibit
automated access; the spec itself requires respecting provider terms. Spec v1 forbade it outright.
**Decision:** Build the bridge as a pluggable, isolated service with full state machine and MCP tools, labelled
EXPERIMENTAL and **disabled by default**. Each provider adapter stays locked until the Super Admin records an explicit
acknowledgement of that provider's terms (stored in the audit log). Official APIs and official CLIs remain the primary
execution paths. Never touch personal browser profiles, never read/export cookies, never bypass CAPTCHAs.
**Consequences:** The capability exists and is testable (against a local test page) without putting the owner's accounts at risk by default.

## ADR-006 — Auth: local accounts, JWT in HttpOnly cookie, CSRF double-submit, 6 roles (2026-11-09)
**Decision:** Keep argon2id + JWT cookie. Add CSRF double-submit token for state-changing requests. Roles:
SUPER_ADMIN, ORGANIZATION_ADMIN, PROJECT_MANAGER, DEVELOPER, REVIEWER, VIEWER; permissions checked server-side per route.
Agents have a separate *agent security profile* and can never hold user roles.

## ADR-007 — Multi-tenant ready, single tenant operated (2026-11-09)
**Decision:** `organizations` and `teams` tables; org-scoped tables carry `organization_id`; every scoped query goes
through a tenant-filter helper. One default organization is created at bootstrap. No per-tenant databases.

## ADR-008 — Docker sandbox via Docker Engine API (2026-11-09)
**Decision:** Agent commands run in throwaway containers: `--network none` by default, CPU/memory/pids/time limits,
read-only root FS, only the task's worktree mounted, no host secrets, non-root user. Command policy is default-deny
with allowlists derived from tool permissions.

## ADR-009 — Memory retrieval: PostgreSQL full-text first, embeddings optional (2026-11-09)
**Context:** Semantic retrieval needs embeddings, which need a provider key the platform may not have.
**Decision:** Hybrid ranking — PostgreSQL full-text + trigram similarity always available; an `embedding` column is
filled when an embedding-capable provider is configured, and cosine similarity is then blended in.
**Consequences:** Retrieval works offline and in tests; quality improves when a provider is configured.

## ADR-010 — Offline verification images for the cloud dev environment (2026-11-09)
**Context:** The cloud dev environment blocks every container registry.
**Decision:** `docker/offline/` rebuilds equivalent services on a debootstrap'd Ubuntu base, used only via an override
compose file to test the real `docker-compose.yml` end-to-end. Owner machines use official images.

## ADR-011 — Frontend: next-intl cookie locale, self-hosted fonts, TanStack Query + Zustand (2026-11-09)
**Decision:** Locale in `NEXT_LOCALE` cookie and user profile (no locale URL prefix). Fonts via @fontsource (no network
at build). Server state with TanStack Query, UI state with Zustand, forms with React Hook Form + Zod.

## ADR-012 — Rebuild the schema as a v4 baseline migration (2026-11-09)
**Context:** Spec v4 Phase 19 renames and adds many entities (29 core). No production data exists yet.
**Decision:** Replace the v1 initial migration with a single `v4 baseline schema` migration (40 tables) instead of
chaining rename migrations. From now on every schema change is a new incremental Alembic revision.
**Consequences:** Development databases created before 2026-11-09 22:40 must be recreated (`RESET-DEV`); no owner data affected.

## ADR-013 — One HTTP stack (httpx2) for the gateway; Gemini over REST (2026-11-09)
**Context:** anthropic 2.6 and openai 3.48 SDKs are built on `httpx2` (Anthropic v2 also authenticates with
`Authorization: Bearer`). The spec suggests the Google Gen AI SDK.
**Decision:** All adapters use `httpx2`; the Google adapter calls the Gemini REST API directly (streamGenerateContent
SSE, models list) instead of adding the google-genai SDK. Tests drive every adapter through `httpx2.MockTransport`
with each provider's real streaming format, so SDK parsing is exercised. Revisit google-genai if features beyond
chat streaming (files, caching) are needed.

## ADR-014 — Internal bus: transactional outbox, idempotency ledger, DB work locks (2026-11-09)
**Context:** Executions ran as API background tasks. Phase 6.1 requires durable dispatch with IDs, correlation,
idempotency, retries, DLQ and acks; MQ-04 requires surviving broker and worker restarts.
**Decision:**
- Producers never publish directly. `app/bus/outbox.enqueue()` writes the `Envelope` into `outbox_messages` in the
  same transaction as the state change; the worker's relay publishes with publisher confirms (`FOR UPDATE SKIP
  LOCKED`, so several workers can relay). Broker down → rows wait; nothing lost, nothing phantom.
- Topology (`app/bus/topology.py`): `acc.commands` topic → one classic priority queue per engine specialty
  (`acc.work.execution.<engine>`, priorities 0-9 need classic queues); `acc.events` topic for domain events;
  fanout retry tiers 5s/30s/300s whose TTL dead-letters back to the *origin queue* via the default exchange;
  `acc.dlx` → `acc.dlq` for poison, expired and retry-exhausted messages.
- Consumers (`app/bus/consumer.py`): validate → expiry → PostgreSQL session advisory lock on the work key (e.g. the
  execution) on a dedicated connection → `processed_messages` ledger (unique consumer+idempotency key) → handler →
  ledger then ack. A dead worker's lock vanishes with its connection and the broker redelivers its un-acked message;
  the next worker, holding the lock, knows the previous owner is gone and recovers the execution from its checkpoint.
  A busy key is deferred through a retry tier, never run twice concurrently.
- Business failures stay the runtime's job (its own retry budget); the bus retries only infrastructure failures.
- `EXECUTION_DISPATCH=inline` exists only for unit tests / single-process debugging; production is `bus`.
**Consequences:** at-least-once delivery with exactly-once effects per consumer. The execution state table gained
READY → FAILED (runtime lost before the engine started). Long executions rely on RabbitMQ's consumer timeout
(30 min default) exceeding the agent timeout (default 15 min); raise it with the agent timeout if needed.
