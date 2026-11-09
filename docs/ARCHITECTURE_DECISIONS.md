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
