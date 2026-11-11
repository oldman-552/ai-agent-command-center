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

## ADR-015 — A2A: v1.0 JSON-RPC, key-gated exposure, tasks are executions (2026-11-09)
**Context:** Phase 6.3 asks for A2A between independent agent runtimes, distinct from MCP (agent↔tool) and the
internal bus. A2A v1.0 (JSON-RPC binding: `SendMessage`/`GetTask`/`ListTasks`/`CancelTask`, `A2A-Version` header,
`/.well-known/agent-card.json`, ProtoJSON enum names such as `TASK_STATE_COMPLETED`, `ROLE_USER`).
**Decision:**
- Server (`app/connectivity/a2a/server.py`): one A2A agent per platform agent under `/api/a2a/agents/{identifier}`.
  Nothing is exposed by default: an `a2a_access_keys` row (SHA-256 of a high-entropy token, created only by a human
  with `security.manage`, audited, revocable) lists the agents it may reach. The card itself requires the key, and
  unlisted agents answer 404. Tasks are private to the key that created them.
- An A2A Task *is* an Execution (same runtime, tool gate, budgets, audit; dispatched over the bus). `messageId` is the
  idempotency key. Blocking `SendMessage` waits up to min(agent timeout, 120 s). Only v1.0 is accepted; a request
  without `A2A-Version` (= 0.3 per spec) gets `VersionNotSupportedError` (-32009).
- Streaming (added 2026-11-10): `SendStreamingMessage` / `SubscribeToTask` over SSE, one JSON-RPC response per
  `data:` line (confirmed against the specification source, JSON-RPC binding §9.4.2), StreamResponse objects per spec §4.2 (`task` first; no `final` field in v1.0 — the stream closes).
  The server polls the execution's events every 0.25 s; the `a2a` engine streams when the card allows it.
- Push notifications (added 2026-11-10, spec §3.1.7–3.1.10, §4.3, §13.2): a transactional outbox of webhook
  deliveries written by `set_status`, sent by a worker loop with backoff and per-config ordering; SSRF guard at
  creation and before each delivery. The token is sent as `X-A2A-Notification-Token` (the header of earlier A2A
  versions; v1.0 does not name one).
- Extended card and multi-turn (added 2026-11-10): context inheritance per (key, agent, contextId), bounded and
  fenced like other untrusted context; follow-ups to finished tasks become new tasks that reference them, because
  finished executions are immutable evidence. Not yet: input-required mid-task.
- Client (`client.py`): the RPC URL must come from the card's JSONRPC interface **on the same host** as the card, so a
  hostile card cannot redirect the bearer token; only http(s); size-capped, validated responses; transport errors →
  retryable.
- Engine `a2a` (`engine.py`, EE-03): a platform agent whose provider is a Provider of kind `a2a` (auth type
  "external agent service") delegates to the remote agent; the remote task id is checkpointed and the messageId is
  derived from the execution id, so a resumed execution re-attaches instead of resubmitting.
**Uncertainty:** the v1.0 spec pages available rendered some data-model tables incompletely; `securitySchemes`
(`httpAuthSecurityScheme`) and `securityRequirements` follow the ProtoJSON mapping of the proto and should be
re-checked against `spec/a2a.proto` when interoperating with a third-party SDK.

## ADR-016 — Orchestrator: LangGraph for planning only; scheduling is stateless over the bus (2026-11-10)
**Context:** Phase 4 asks for planning, dependency graphs, assignment, parallelism, review and recovery, "using
LangGraph where appropriate".
**Decision:**
- Planning (`app/orchestration/planner.py`) is a LangGraph graph `draft → validate → (retry draft with the validation
  error | persist | give_up)` with `AsyncPostgresSaver`, thread id = workflow.thread_id, so an interrupted plan resumes
  at the next node and the model is not called again. LangGraph owns its tables in schema `langgraph` (its table name
  `checkpoints` collides with ours). Plans are untrusted output: schema-validated, ≤ 25 tasks, cycle-checked.
- Scheduling (`scheduler.py`) is *not* a long-running graph: `advance()` re-derives everything from PostgreSQL under a
  `SELECT … FOR UPDATE` on the workflow row, so any worker can run it any number of times after any restart. It is
  driven by `workflow.advance` commands and `execution.finished` events on the bus (ADR-014).
- Selection (`selection.py`): hard filters (tenant, enabled, available, engine, project roster, all required skills,
  free concurrency) then a transparent weighted score (role fit, recent success rate, load, cost, latency); the
  breakdown is audited per assignment. The planner is also limited to the project roster.
- A finished execution moves its task to REVIEW with evidence (execution id, output hash, mock flag); only a review
  decision completes it (approve / revise → re-run with the note / reject). The automated evaluator (EV-01) will plug
  into the same gate; critical-operation approvals (SEC-03) are still to come.
- Task executions use idempotency key `task:{id}:attempt:{n}`; stale or duplicate `execution.finished` events are
  ignored unless they match the task's current attempt.
**Consequences:** worker kill mid-workflow and duplicate messages are harmless (tested over a live broker). Waiting on
busy agents re-polls through the bus delay tier; waiting on a missing skill waits for a human nudge
(`POST /api/workflows/{id}/advance`).

## ADR-017 — Approval Center: frozen payloads, human-only, never on one's own request (2026-11-10)
**Context:** Phase 16 — critical operations must be blocked until approved, with full records, without stopping work.
**Decision:** `app/governance/approvals.py` holds the catalogue of critical operations with a minimum risk each (a
requester can raise but never lower it). A request freezes the exact payload and stores its SHA-256 as the first
evidence item; only `approvals.decide` (HUMAN_ONLY) can decide, never on a request the same user made; reject /
changes-requested need a reason; pending requests expire after 7 days. On approval the platform runs the registered
executor for that operation exactly once, after re-checking the payload hash; a changed payload or an executor error
is recorded as evidence and leaves the request APPROVED-but-not-executed (the decision itself is never lost).
Executors cannot exceed agent security-profile ceilings. Built-in executor: `permission.escalation`.
Tasks may declare a `critical_operation`; after a passing evaluation and human review they wait in APPROVAL, while
independent tasks continue; the decision settles the task (approve → COMPLETED, changes → READY with the note,
reject → FAILED, dependents BLOCKED).
**Scope note:** humans acting directly with HUMAN_ONLY permissions (e.g. rotating a provider key) are themselves the
authorization; the center gates what agents and automation request. Executors for deploy/destructive-DB/merge do not
exist yet — those approvals stay APPROVED until their planes (M8 GitHub, M13 sandbox) register an executor.

## ADR-018 — Evaluation engine: deterministic checks decide, a reviewer model can only withhold (2026-11-10)
**Context:** Phase 14 / OR-08 — tasks must never complete on an agent's claim; do not rely on another LLM alone.
**Decision:** `app/evaluation/engine.py` evaluates every completed task execution before it can reach REVIEW:
execution completed, output present, each acceptance criterion evidenced in the output (transparent keyword-coverage
heuristic, threshold 0.6, reported per criterion), no policy-denied tool calls, within task budget. An optional
reviewer agent on the project roster (tool `pr.review`) gives per-criterion verdicts that can turn approve into revise
but never the reverse. Scores per Phase-14 dimension; dimensions that need the sandbox (tests, lint, security,
regression, architecture, documentation) are stored as null, not guessed. Revise/reject sends the task back to READY
with the evaluator's notes until max_attempts, then FAILED. A human cannot approve a task whose latest evaluation did
not pass (409 `evaluation_not_passed`). Results are stored per execution (idempotent on redelivery).
**Limitation:** keyword coverage is a weak proxy for "criterion met"; it is the gate until sandboxed tests exist
(SEC-04), when test results become the primary signal.

## ADR-019 — Sandbox: one throw-away Docker container per command, default-deny argv policy (2026-11-10)
**Context:** Phase 17 — all agent code execution isolated, least privilege, default deny, no secret leakage.
**Decision:** `app/sandbox/runner.py` runs each command with `docker run --rm` and: `--network none`, `--cpus`,
`--memory` = `--memory-swap`, `--pids-limit 256`, time limit (docker kill → exit 124), `--read-only` rootfs with a
256 MB `/tmp` tmpfs, `--cap-drop ALL`, `no-new-privileges`, uid 10001, `--ipc none`, a fixed minimal environment
(nothing from the platform process), and only the project workspace bind-mounted at `/workspace`. Workspaces live at
`<WORKSPACES_DIR>/<org>/<project>`, are resolved with symlinks and refused if outside that root, and are owned by the
sandbox uid. Output is capped at 256 KB per stream and secret-redacted.
`app/sandbox/policy.py`: commands are argv lists executed without a shell; each tool permission has its own
allow-list of command prefixes (`shell.test` cannot build, `shell.build` cannot test); absolute paths outside
`/workspace`, `..`, and code-loading flags are refused. Denials and runs are audited (`sandbox.*`).
The evaluator now uses the last `shell.test`/`shell.build` results as deterministic checks (`tests_pass`,
`build_or_lint_pass`) and fills `test_pass_rate` / `code_quality`.
**Deployment note (open):** the worker needs a Docker daemon. Mounting the host's docker.sock into the worker container
would give it root-equivalent power over the host; the compose stack therefore does not wire it yet. Options for the
owner's machine: run the worker on the host, or a rootless Docker / dedicated sandbox daemon. Tracked as a known issue.

## ADR-020 — Recovery workflow, circuit breakers and graceful shutdown (2026-11-10)
**Context:** Phase 15 — identify failure category, preserve evidence, retry only when safe, avoid infinite retries,
circuit breakers, graceful shutdown with partial-result preservation.
**Decision:**
- `service.categorize()`: policy (tool denied) · timeout · transient (RetryableError) · provider (configuration/auth —
  retrying the same way will not help) · error. Only *transient* is retried, within the agent's retry budget.
- On final failure the runtime keeps any partial output (`output.partial = true`) and writes a `recovery` event:
  category, evidence count, last checkpoint, retry-safe, attempts vs budget, fallback used, circuit state, outcome
  `escalated` and the next step; `recovery.escalated` is audited.
- `circuit_breakers` table, scopes `agent` and `provider`: N consecutive health failures (default 5; policy denials and
  cancellations don't count) open the circuit; after the cool-down (default 300 s) exactly one trial passes
  (half-open); success closes it, failure re-opens it. Open agents cannot get executions (409 `agent_circuit_open`)
  and are skipped by assignment; open providers are skipped by the gateway, which goes straight to the fallback.
- Graceful shutdown (worker SIGTERM): stop consuming, give in-flight work `SHUTDOWN_GRACE_SECONDS` (default 30) to
  finish, then interrupt the rest: they keep partial output and their checkpoint, become TERMINATED, and their
  un-acked messages are redelivered to another worker which resumes them. A retry interrupted during its back-off is
  continued too.

## ADR-021 — Development plane: hardened local git, one worktree per task, merges only via approval (2026-11-10)
**Context:** Phase 12 — isolated worktrees and feature branches per agent, tracking of files/ownership/conflicts,
agents never modify protected branches, merge after human approval. Repository content is untrusted (Phase 17).
**Decision:**
- `app/devplane/git.py` runs git with a scrubbed environment (no global/system config, no credentials), hooks disabled
  (`core.hooksPath=/dev/null`, also overriding a repo's own setting), no fsmonitor/external diff/ssh, fixed argv.
- Project repository at `<WORKSPACES_DIR>/<org>/<project>/repo` (default branch checked out; new-project mode creates
  it with an initial commit). Each task gets `wt/task-<id>` on `agent/<agent-identifier>/task-<id>` from the default
  branch. Tools `repo.read`, `files.write`, `git.branch`, `git.commit` work only inside that worktree: relative paths,
  no `..`, no `.git`, no symlink traversal, 1 MB per file. Commits carry the agent as author and the platform as
  committer. Sandbox shell tools mount the task's worktree, so tests see exactly the agent's changes.
- Protected branches (repository rule or `main`/`master` + the project default) can never be an agent's write target;
  a worktree moved off its branch (detached or switched) cannot commit. Attempts are audited.
- Tracking per worktree: modified files vs base, overlaps with other active worktrees, textual conflicts with the
  base branch (`git merge-tree --write-tree`), commit history, status active → committed → merged.
- Integration: a reviewed task with committed changes requests `git.merge_protected` in the Approval Center with the
  frozen head commit; textual conflicts block review approval. The executor merges `--no-ff` only if the branch tip
  still equals the approved commit; otherwise (or on conflicts) the approval stays un-executed and the task returns to
  READY with the reason.
**Not yet:** GitHub remote (auth, push, PRs, webhooks, CI) — GH-01/02/05/07/08.

## ADR-022 — GitHub: encrypted tokens, push via askpass, PRs with evidence, merges pinned to the approved SHA (2026-11-10)
**Context:** Phase 12 GitHub integration on top of ADR-021's local worktrees.
**Decision:**
- Connecting a repository requires `credentials.manage` (human-only); the token is verified against GitHub
  (`GET /repos/{owner}/{name}`), stored encrypted as an ApiCredential, and only a 4-character hint is ever returned.
  `api_base` supports GitHub Enterprise (https only).
- `app/devplane/github.py`: REST client (repo, branches, commits, compare, pulls, check runs, create/merge PR) with
  GitHub's versioned headers; network errors, 5xx and rate limits are retryable, everything else is not.
- Push (`pr.create` tool): only agent branches (protected names refused), only https remotes; the token reaches git
  through a GIT_ASKPASS helper reading an environment variable — never argv, URL or logs; credential helpers off.
- The PR description is generated from evidence: acceptance criteria ticked by the evaluator, last sandbox test run,
  evaluation decision/score, changed files, overlaps/conflicts, and a mock warning when relevant.
- Merge: when the task has an open PR the `git.merge_protected` executor calls GitHub's merge API with
  `sha = approved head commit`, so GitHub itself refuses (409) if the branch moved; failure returns the task to READY.
- Webhooks `POST /api/github/webhook/{repository_id}`: HMAC-SHA256 (`X-Hub-Signature-256`, constant-time compare)
  with a per-repository encrypted secret; unknown repository and bad signature answer the same 401; each
  `X-GitHub-Delivery` is applied once; `check_run`/`status` update PR checks, `pull_request` updates state.
**Verification limits:** github.com is unreachable from the development environment; the client is tested against an
in-memory fake implementing the same endpoints and pushes go to a real local bare repository. A live check against a
real repository must be done on the owner's machine.

## ADR-023 — Agent Supervisor: separate role, findings with fingerprints, a closed list of reversible actions (2026-11-10)
**Context:** Phase 5 — independent monitoring of agent-team health that may take reversible, policy-authorized actions,
must log everything, and must never override security policy or approvals.
**Decision:**
- `app/supervisor/` runs as its own worker role (`WORKER_ROLES=supervisor`; on by default), one leader across all
  workers via a PostgreSQL advisory lock, sweeping every `SUPERVISOR_INTERVAL_SECONDS` (30).
- Detectors: stalled executions (no event for 300 s), runaway cost/token budget, excessive retries, repeated agent
  failures (policy denials excluded), suspicious tool usage (repeated policy denials), idle agents while compatible
  work waits, workflow deadlocks, duplicate tasks, conflicting worktree changes, retryable transient task failures.
- Findings are deduplicated by fingerprint (kind + subject), keep `last_seen_at`, and resolve themselves when the
  problem disappears. Every detection and action is audited (`category=supervisor`).
- Actions are a closed list — stop_runaway, resume_stalled (via the bus; the execution lock makes it safe),
  isolate_agent (open its circuit; releasable), retry_task (only transient failures within budget), reduce_concurrency —
  and each can be switched off with `SUPERVISOR_ACTIONS` (empty = advisory only). Deadlocks, duplicates and conflicts
  are reported, not acted on.
- SV-05: the module has no path to approvals, permissions, merges or credentials, and never acts on a task in REVIEW,
  APPROVAL, COMPLETED or CANCELLED — enforced by code structure and tested.

## ADR-024 — Context & memory: trust-separated assembly, scoped memory, local hybrid retrieval (2026-11-10)
**Context:** Phases 7–8 — agents need relevant, budgeted context per task type; memory must be scoped, carry
provenance, separate verified knowledge from assumptions, be correctable by humans, and never leak across projects.
Retrieved text and tool output are attacker-controllable (prompt injection) and must not override platform policy.
**Decision:**
- `app/memory/store.py` over the existing `memories` table: nine types, each with a mandatory scope (project types
  need a project, `agent_identity` an agent, `team_shared` a team). Provenance in `source`; only a human (`kind=user`)
  can make a memory `verified` — agents' `memory.write` tool stores `suggestion`s with the execution as provenance.
  Corrections create a new version (`supersedes_id`, old row `is_current=false`), deletion removes the whole chain,
  expired or non-current rows are never retrieved. Text is secret-redacted before storage. Everything is audited.
- Retrieval (MEM-02) is hybrid and fully local: PostgreSQL `ts_rank` on the existing GIN `tsv` column + cosine of a
  deterministic 256-d hashed word/char-trigram embedding stored in `embedding` (works for Arabic and English, no
  external model, no pgvector dependency). Verified memories get a small boost, suggestions a small penalty.
  Follow-up (2026-11-10): an optional provider embedding is stored *next to* the hashed one (`semantic_embedding`,
  `semantic_model`), indexed by a worker loop, and used only when made by the configured model; query embedding is
  bounded (timeout, cache, breaker) and falls back to hashed vectors. Off by default because it sends memory text out.
- Isolation (MEM-03): every query is filtered by organization, and a project query admits only that project's rows
  (plus project-less agent/team rows of the same organization). The API checks the scope objects with `get_scoped`.
- `app/context/assembler.py` (CX-01..03): per-task-type profiles (architecture, frontend, backend, database,
  security, testing, code_review, general) set the budget shares and preferred memory types; budget from
  `agent.context_policy.max_context_tokens` (default 8000). Exact duplicates are dropped everywhere, near-duplicate
  memories (cosine ≥ 0.92) are collapsed, items are ranked, over-budget items are compressed extractively
  (head + tail) before being dropped; the manifest records what was kept, compressed, dropped and flagged.
- Trust separation (CX-02): the SYSTEM message is built by the engine from platform policy + the agent's configured
  instructions only (`system_message()`); callers cannot create system/policy segments. Requirements and human notes
  are trusted; memories and prerequisite/tool outputs are fenced in `<<<UNTRUSTED DATA {nonce}>>>` blocks with a
  per-assembly random nonce (a document cannot close its own block), instruction-like content is flagged, and a policy
  reminder follows all untrusted data. This is a mitigation, not a guarantee — the real enforcement remains in the
  backend (permissions, approvals, protected branches) which no prompt can change.
- UI `/memory`: browse/search per project, verify, correct (with history), delete, and a context preview per task.

## ADR-025 — Browser Bridge: own Python/Playwright service, gate checked twice, no secrets path (2026-11-10)
**Context:** Phase 11 asks for a browser bridge inspired by `YosefHayim/ai-browser-bridge`. That project is MIT but
macOS-only, Node-based, uses one shared Chrome profile and publishes an MCP server through a public tunnel
(evaluation in `docs/BROWSER_BRIDGE.md`). ADR-005 (owner-approved) requires disabled-by-default and per-provider terms.
**Decision:**
- Re-implement in Python with Playwright as a separate service (`python -m app.browser.server`, port 8100, localhost
  or private network, internal HMAC token). It is the only process that owns browsers; the API, the `browser`
  execution engine and the MCP tools call it through `app/browser/client.py`.
- Sessions live in `browser_sessions` (state, history of visible text, error); live browser objects only in the
  bridge process. On bridge start, sessions left active are marked DISCONNECTED.
- One persistent Chromium context per session in `BROWSER_PROFILES_DIR/<org>/<provider>/<profile>`; no CDP port,
  downloads disabled, profile names validated, one live session per profile, a global session limit.
- Gate (`app/browser/gate.py`): organization setting `browser_bridge = {enabled, acks}`; changing it needs the
  human-only `browser_bridge.manage` (Super Admin only — removed from Organization Admin); the exact terms statement
  must be accepted; checked by the API and again by the bridge on each start/prompt.
- Detection, not interaction: CAPTCHA/sign-in → AUTH_REQUIRED (a person acts in the bridge window and presses
  "check again"); missing elements → FAILED `interface_changed`; the answer is read with `inner_text` only.
- Adapters are data (selectors). Only the local test adapter is verified; real ones are labelled unverified.
**Consequences:** fully testable offline against a local page; real-provider use stays an explicit, audited owner
decision; compose runs the bridge headless, so real sign-in needs the bridge on the host with a visible window.

## ADR-026 — MCP registry and platform MCP server; static skill assessment (2026-11-10)
**Decision:**
- MCP Python SDK pinned to 1.x (`mcp>=1.20,<2`, 1.30 verified): its client is used for stdio and streamable HTTP
  servers; registration stores an assessment and leaves servers and tools disabled; agents reach tools through one
  runtime handler for `mcp:<server>:<tool>` that enforces enabled server/tool, assignment and permission key.
- The platform's own MCP server is a FastMCP instance served statelessly at `/api/mcp/` with a fresh
  StreamableHTTPSessionManager per application lifespan (the SDK manager can only run once). Each tool call
  re-authenticates the bearer key from the request (contextvars do not cross the manager's task group).
- Skills come only from host folders (`SKILLS_DIR`) or uploads; a regex-based static scanner classifies findings into
  the spec's categories; HIGH risk requires an audited acceptance to enable. Enabled + assigned skills feed the
  agent's task context.
**Limits:** the scanner is heuristic (false positives and negatives are possible) — it informs a human decision, it is
not a sandbox; skills never execute code on the platform.

## ADR-027 — Routing, budgets, teams and existing-project analysis (2026-11-10)
**Routing (GW-07):** agents opt in with `context_policy.routing = "auto"`. `gateway/routing.py` filters every enabled
model by availability (provider enabled, circuit not open, provider budget not exhausted), capabilities (tools when
the agent has tools, vision for images, `required_capabilities`), context window and user configuration
(`allowed_provider_ids`, `excluded_models`, mocks only with `allow_mock`), then ranks by complexity fit (task type,
prompt size or an explicit hint → tier 1–3), estimated cost from configured prices only, observed latency, recent
success ratio and provider priority. The top three become the route list (fallback order). Each decision is audited
(`gateway.routed`) and shown as an execution log event; `POST /api/routing/preview` explains a decision without
running anything. LiteLLM was not added: our adapters already cover the providers in scope (ADR-004).
**Budgets (GW-08):** `governance/budgets.py` computes spend from recorded cost rows and real (non-mock) usage rows per
scope (global/agent/project/task/provider) and period (daily/monthly/total); task and project `budget_usd` are
implicit total budgets. `create_execution` refuses new work in an exhausted scope with HTTP 402 `budget_exceeded`;
the stop is audited and a deduplicated `budget.increase` approval (+50 %, frozen payload) is opened in a separate
transaction so the caller's transaction is untouched. The orchestrator treats it like any refusal: that task waits,
the rest continues. Approving raises the limit by exactly the approved amount.
**Teams (AG-07):** `/api/teams` CRUD with agent and user membership, an optional lead (must be a member; removed
leads are cleared), projects can belong to a team, membership is tenant-checked and audited.
**Existing projects (OR-09):** `/api/projects/{id}/import` clones the linked GitHub repository into the project
workspace with the hardened git (askpass, no hooks); `/analyze` stores a static map (languages, frameworks,
infrastructure, architecture roles, dependencies, tests, technical debt) — nothing is executed. Planning for
`existing`/`maintenance` projects includes the map, fenced as data.

## ADR-028 — Redis for live updates and heartbeats; OpenTelemetry; in-app automations; 20-item navigation (2026-11-10)
**Redis (FND-14):** used only for ephemeral data — pub/sub of live updates and service heartbeats (TTL 45 s).
Nothing durable lives there; losing Redis degrades to polling.
**Live updates (UX-02):** `log_activity` and execution events queue `{topic, ids}` on the SQLAlchemy session; an
`after_commit` hook publishes them to `acc:live:<org>` (rolled-back work publishes nothing). `GET /api/ws` (through the
Next.js proxy) authenticates with the session cookie, requires the CSRF cookie value as the first message
(cross-site WebSocket hijacking), and relays only the user's organization channel. Messages carry no content; the
browser refetches through the normal permission-checked API. Bursts are coalesced; reconnect with backoff.
**Observability (UX-05):** JSON logs with trace/span ids and redaction (`LOG_FORMAT=text` for local); OpenTelemetry
spans for HTTP requests (FastAPI instrumentation), `execution.run` and `bus.consume`; OTLP/HTTP export when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set. `/api/health` reports database, RabbitMQ, Redis, worker heartbeats (with roles)
and the Browser Bridge; 503 only when the database is down, otherwise `degraded` names what is missing. Trace context
crosses RabbitMQ: `enqueue()` stores the active W3C `traceparent`/`tracestate` on the outbox row (not in the
versioned envelope), the relay sends it as AMQP headers, and each consumer span is a child of it; retries carry the
consumer's own context.
**Automations (UX-06):** `automations` + `automation_runs`; triggers cron (UTC, croniter, ≥ 5 min), interval (≥ 5
min) and audit events (cursor, no replay of history, no automation→automation loops); actions submit an objective or
re-analyze the repository. Run by the worker role `automations` (advisory-lock leader). The creator's (last editor's)
permissions are re-checked at run time. Pause/resume (no catch-up storm)/edit/run now/delete/history. Never installs
anything into the operating system.
**Navigation (UX-01):** the spec's 20 items, each a working page (new: Projects, Agent Teams, Orchestration,
Executions with timeline, MCP & A2A, Agent Runtime, Skills, Analytics with budgets, Settings with users). Templates are
reached from Agents and Settings.

## ADR-029 — CLI engine in the sandbox; launchers through Docker Compose only (2026-11-10)
**CLI engine (EE-02):** coding CLIs run non-interactively in a fresh sandbox container (`acc/cli:latest`, built by the
owner from `docker/cli/Dockerfile`) with only the task worktree mounted and network enabled (the CLI must reach its
API). The API key comes from a platform credential and is passed with `docker run --env NAME` (value in docker's own
environment, never in argv) and masked in output; consumer subscriptions are never used. Only documented flags are
used; how each was verified is recorded in PROVIDER_INTEGRATION.md. CLI-reported costs are not recorded as platform
cost. Approval-bypass flags (`--yolo`, `--dangerously-skip-permissions`) are never used.
Workspace trust (2026-11-10): Gemini CLI's headless mode refuses an untrusted folder, so it runs with `--skip-trust`.
Trust makes a CLI load project configuration from the repository, which can widen approvals while the key is in the
environment, so the engine refuses any workspace that contains the CLI's project configuration (`.gemini/`, `.codex/`,
`opencode.json(c)`, `.opencode/`) unless the agent's policy sets `cli_allow_project_config`. Claude Code's `--bare`
already skips project configuration.
**Image hygiene (SEC-08, 2026-11-10):** every Dockerfile applies pending OS security updates at build time;
`scripts/check-image-updates.sh` (run by `security-scan.sh`) reports published-but-missing security updates per
image without a vulnerability database, because Trivy's database is unreachable in the development environment.
**Launchers (FI-01/02):** thin `.bat` wrappers call one PowerShell script (`scripts/windows/acc.ps1`, UTF-8 BOM,
CRLF); Linux/macOS scripts share `scripts/lib/acc.sh`. Both only drive Docker Compose: check Docker, create `.env`
with generated secrets (asking for the Super Admin password; never a fake credential), build, start, wait for health,
open the browser, log to `logs/`. Stop never removes volumes; reset requires typing `RESET`. `docker-compose.prod.yml`
adds restart policies, limits, secure cookies and loopback-only ports for a reverse proxy.

## ADR-030 — Model tool calling in the API engine (2026-11-10)
**Context:** The data plane must invoke tools (spec: Data Plane → Tool invocation). The `api` engine ran a single chat
turn, so agents on real models could not use their permitted tools; tests had not shown it because the mock engine
calls tools from a script.
**Decision:**
- One provider-neutral conversation format (user / assistant with `tool_calls` / tool results) kept by the engine;
  each adapter converts it to its API and parses tool calls from its stream. Tool names are encoded for the
  strictest provider rule. Gemini schemas are reduced to its supported subset.
- Offered tools = permitted ∩ implemented-with-schema, plus enabled tools of assigned MCP servers that are permitted.
  Being offered grants nothing: calls go through `service.call_tool` (permission check, policy, audit,
  `WAITING_FOR_TOOL`). Denials and unknown tools become error results for the model.
- Bounded loop (`max_tool_rounds`, result size cap), checkpoint per round with the conversation and the model;
  resumption continues on that model and never re-runs finished tool calls; no fallback switch after tools ran.
**Consequences:** Agents on Anthropic, OpenAI(-compatible) and Gemini models can read and write the worktree, run
allow-listed tests/builds, commit, open PRs, propose memories and call MCP tools within their permissions.
Verified with simulated streams and the mock model; live verification needs an API key (GW-10).
Follow-up: `ask.human` parks the execution in `WAITING_FOR_INPUT` via `InputRequired` after a checkpoint that carries
the pending question; `answer_input` appends the answer as that call's result in a new checkpoint and re-dispatches.
A worker skips parked executions unless their question has been answered, so redelivery never resumes one early.

## ADR-031 — Browser Bridge: owner-chosen sites, profiles per person, desktop mode (2026-11-10)
**Context:** The owner wants the bridge to serve arena.ai only plus sites he adds himself, with one browser profile per
employee (each signed in to their own Arena account) that he can watch in real windows on his computer.
**Decision:** Built-in sites are reduced to arena.ai (and the hidden local test page); other sites are data in the
organization's settings, added by the Super Admin and locked until their terms are acknowledged (ADR-005 still
applies; arena.ai's statement names its specific clauses). Profiles become per person/agent/account and shared across
sites, registered with an owner, one window at a time. Desktop mode runs the bridge process on the host with
`BROWSER_HEADLESS=false`, bound to Docker's host-gateway address only, with PostgreSQL/Redis published on 127.0.0.1.
**Consequences:** No adapter is verified against a live provider; selectors are adjustable per site. Desktop mode is
Linux-desktop only for now; in Docker/Codespaces the bridge stays headless.

## ADR-032 — Arena Local Bridge is the only Arena path; the Browser Bridge loses its Arena site (2026-11-11)
**Status:** Accepted. Supersedes the Arena part of ADR-031. Assessment: `docs/WORKFORCE_ASSESSMENT.md`.
**Context:** The owner wants one prompt handled by a Claude Manager and four specialised workers, each on its own
Arena account, through Arena Local Bridge (ALB, `Gandhara2077/arena-local-bridge`). Until now the only Arena path was
the built-in `arena` site of the Playwright Browser Bridge: guessed DOM selectors that were never checked against the
live page, one browser profile per person, and no way to plan, review or consolidate work. ALB already does the
browser work and exposes an OpenAI-compatible HTTP endpoint on the Windows host, so two parallel Arena paths would
mean two sets of sessions, two terms gates and an unverified scraper next to the supported route.
The v1 spec said "never use unofficial Arena AI integrations"; this ADR records that the owner has explicitly
replaced that rule for ALB. ALB is not an official Arena API, and the terms risk noted in ADR-031 remains.
**Decision:**
1. **Exclusive path.** Arena is reached only through the provider kind `arena_local_bridge`. The Browser Bridge's
   built-in `arena` site, its selectors and its terms text are deleted. Any site whose host is `arena.ai` or
   `lmarena.ai` (or a subdomain) is refused when added, dropped from the catalogue if it already exists, and refused
   again by the bridge service. Stored Arena acknowledgements, selector overrides and custom Arena sites move to
   `browser_bridge.retired_arena` (kept, unused); Arena browser sessions are stopped with an explanatory error. The
   migration is reversible and deletes nothing.
2. **The generic Browser Bridge stays.** Custom sites, profiles, the test page, desktop mode, the `browser` engine,
   the MCP browser tools, the `browser_bridge.manage` permission and the health entry are general-purpose and keep
   working.
3. **A bridge instance is a provider.** Base URL, encrypted write-only key, status and discovered sessions reuse the
   provider registry. Per-instance options live in `providers.settings`; the last health snapshot in
   `providers.health`. Several instances can exist (one per Arena account gives real parallelism).
4. **Own HTTP adapter, not the OpenAI SDK.** It needs explicit control of what ALB actually implements:
   - `Host: 127.0.0.1:<port>` — ALB answers 403 to any other Host on every route, whatever its `HOST` setting, so
     this is the only way a container can reach it through `host.docker.internal`. The guard exists against browser
     DNS rebinding; the Bearer key is still required and ALB can stay bound to loopback. The header is a visible
     per-instance setting.
   - `x-codex-session-id` (stable per worker), `x-arena-idempotency-key` (stable per execution round).
   - the system prompt folded into the user message, because ALB sends only the last user message to a session;
   - streaming with ALB's 10-second heartbeats as the liveness signal; a client-side idle and total timeout, because
     ALB has none; a non-streaming mode as a switch;
   - ALB's error body, `Retry-After`, in-stream error frames and `X-Arena-Bridge-Request-Id` mapped to the gateway's
     error classes and to the execution's events;
   - `usage` is ignored: ALB always reports zeros, so usage is recorded as *not reported*, never as 0 tokens.
   The adapter never calls `/api/status` (it returns the bridge key) and never uses ALB's reCAPTCHA or proxy helpers.
5. **Account binding is shown as what it is.** ALB has no account selector and names no account in its responses.
   A worker is *deterministic* when its model is a session UUID (the session's owner is always used) or when its
   bridge instance has exactly one enabled account; otherwise it is *pooled* and the UI says so. The owning account
   is read from ALB's session registry, stored masked, and labelled "configured, not attested per response".
6. **Terms gate.** No chat request goes to ALB until a person accepts an exact statement for that instance. The
   statement names the instance's address, so changing the address revokes it. Audited and revocable. Diagnostics and
   health readings need no acknowledgement: they use ALB's local routes and its `ping` probe model, which ALB answers
   itself without opening Arena. Human-Assisted mode needs no bridge and no gate.
   Creating a bridge, changing its address or options, replacing its key, acknowledging or revoking the terms and
   deleting it need `workforce.bridge`, a human-only permission that only the Super Admin role holds: the bridge
   drives the owner's personal Arena accounts, so an Organization Admin can configure workers (`workforce.manage`)
   and run diagnostics but cannot point the platform at another bridge or accept the terms on the owner's behalf.
   Bridges are managed only at `/api/workforce/bridges`; the generic provider routes answer 409
   `managed_at:/api/workforce/bridges` for this kind, so the address rules and the gate cannot be bypassed there.
7. **No duplicate turns after a restart.** The idempotency key is stable across resume and transient retries. A
   request whose outcome is unknown is resent only inside ALB's replay window; after it the execution fails as
   `outcome_unknown` and the task-level policy (bounded retry or Human-Assisted fallback) decides.
8. **Fake bridge for tests.** `backend/devtools/fake_arena_bridge.py` reproduces ALB's routes, host guard, errors,
   queue and stream format from its source. It identifies itself (`service: fake-arena-bridge`), and a provider that
   talks to it is forced to `is_mock = true`, so its results are labelled mock everywhere.
9. **Only local addresses.** A bridge address must be loopback, `host.docker.internal`, a private IPv4 address, or a
   host named in `ARENA_BRIDGE_ALLOWED_HOSTS`; no credentials, path or query in the URL. The key is therefore never
   sent to a public host.
10. **Health is read, not assumed.** The worker's `workforce` role (one leader, PostgreSQL advisory lock) reads each
    enabled bridge every `ARENA_BRIDGE_HEALTH_INTERVAL_SECONDS`: `/health`, the key probe and the session registry.
    Account e-mail addresses are masked before they are stored (`o***@domain`) and told apart by a salted hash;
    prompts in ALB's registry are dropped. "Works" means reachable, key accepted and an account ready.
11. **The platform waits for a slot before it sends.** ALB runs one turn at a time and refuses when its queue is
    full. Each worker process therefore keeps at most `max_concurrent` (default 1) requests in flight per bridge
    address and makes the rest wait on its own side, where nothing has been sent yet. The wait is recorded on the
    execution. Several worker processes each keep their own count; ALB's queue orders them.
**Consequences:** One Arena path with a clear provider abstraction; the unverified Arena scraper is gone. Automatic
workers need ALB running on the host and the owner's acknowledgement; without it every worker still works in
Human-Assisted mode. Limits inherited from ALB: no per-response account attestation, one turn at a time per instance,
no token usage, at-least-once delivery, no platform tools inside Arena sessions.
**Not verified in development:** a real ALB, real Arena accounts, `host.docker.internal` reaching a loopback-bound
ALB on Docker Desktop, and one-instance-per-account. These need the owner's Windows machine.

## ADR-033 — AI Workforce: one Manager, four workers, results accepted only on recorded evidence (2026-11-11)
**Status:** Accepted. Builds on ADR-032 (the bridge). Code: `backend/app/workforce/`.
**Context:** The owner gives one prompt and wants a Manager model to analyse it, split it into tasks, give them to
four specialised workers, review what comes back and hand over one result. The workers are text-only chat sessions
(Arena accounts behind Arena Local Bridge) or people who paste a task into Arena themselves. The platform already had
an orchestrator (ADR-013..016) for tool-using agents; it assumes an agent can run tools, be evaluated by commands and
retried freely. None of that holds for a chat session that can only answer with text, may deliver the same message
twice, and whose account the platform cannot attest.
**Decision:**
1. **A separate plane beside the orchestrator.** An objective is a `Workflow` with `kind = "workforce"`. It has its
   own planner, scheduler, reviewer and report (`app/workforce/`). The standard orchestrator skips these workflows,
   the standard agent selection never picks the Manager or a worker, and the generic agent and task-review routes
   answer 409 `managed_at:/api/workforce/…` for them, so there is one place where each rule is enforced. Shared and
   unchanged: runtime, gateway, bus, outbox, Approval Center, budgets, audit, RBAC, tenancy.
2. **The Manager is an agent whose model a person chooses.** There is no default model: until one is set, submitting
   an objective answers 409 `manager_model_not_configured`. A bridge session cannot be the Manager
   (`manager_cannot_use_a_bridge`): the Manager needs a system prompt, structured answers and reported usage. Every
   Manager call goes through the gateway and is stored as a `ManagerStep` (analyze, plan, review, consolidate) with
   model, times, outcome, trace and provider request id.
3. **Planning is a checkpointed graph.** analyze → plan → validate → assign, or give up (LangGraph, PostgreSQL
   checkpoints, thread = the objective). A reply that is not valid JSON, breaks the schema or fails validation
   (at most 12 tasks, known workers, at least one acceptance criterion each, no unknown or cyclic dependency, known
   critical operations) is sent back with the error, three times at most. A resumed run continues after the last
   finished step and does not ask the model again. An unreachable model is retried by the bus with back-off; the
   fifth delivery fails the objective with the reason.
4. **Four workers, three modes, nothing automatic by default.** A worker is an agent plus a workforce row: slot,
   role, skills, prompt, model or session, timeout, retries, mode, fallback, optional named person, its own
   conversation id and its account binding (ADR-032 §5). Modes: `AUTOMATIC` (through a provider), `HUMAN_ASSISTED`
   (a person does the task), `DISABLED` (receives nothing). The default workforce is created with every worker
   Human-Assisted and the Manager without a model, so a fresh installation sends nothing anywhere.
5. **One lifecycle table.** A task moves only through `lifecycle.move()`, which checks the `ALLOWED` table and
   writes a history row; a flush hook refuses a lifecycle change made any other way. The coarse task status used by
   the rest of the platform is derived from the lifecycle and cannot diverge. Reopening a failed or rejected task is
   a person's move only.
6. **Scheduling.** A ready task goes to its planned worker if that worker is eligible; otherwise to another eligible
   worker that has every required skill (recorded as `redistributed`); otherwise it waits with a stated reason. A
   task a person assigned by hand is not moved again. One task at a time per worker unless its limit says more.
   A worker that is Automatic but cannot run now (provider off, terms not acknowledged, bridge or account away,
   circuit open) hands the task to a person when its fallback is on, and makes it wait when it is off.
7. **Automatic work is an ordinary execution.** The scheduler creates an execution with the key
   `wf-task:{task}:attempt:{n}`; the runtime and the gateway do the rest, including the bridge's idempotency rules
   (ADR-032 §7). A gateway hook adds the worker's conversation id and refuses the request if the worker is not in
   Automatic mode at that moment, whatever created the execution.
8. **Human-Assisted is a task state, not an engine.** There is deliberately no `human` execution engine: an engine
   kind is a queue with consumers, and a person is not a consumer. A Human-Assisted task waits as a card
   (offer → accept → submit, or decline). The person gets exactly the text a model would get, as one message to
   paste. Attachments are stored on a volume, with an extension allow-list and a size limit, and are only ever
   downloaded, never rendered. This mode needs no bridge, no terms gate and no provider.
9. **A result is a submission, never an HTTP status.** Whatever comes back — from a provider or from a person — is
   stored unchanged as the next immutable version (unique per task and version, with its SHA-256) and only then
   reviewed. A failed Automatic attempt is classified: outcome unknown, account, session or configuration problems
   go to a person (or fail when there is no fallback) and are never resent automatically; bridge down, rate limit,
   upstream error, invalid response and timeout are retried up to the task's attempt limit, then go to a person or
   fail. Every failure is kept on the task with its code, category, attempt, and whether anything was delivered.
10. **The Manager advises; fixed rules decide.** The Manager returns a verdict per acceptance criterion with a quote.
    The platform turns it into one of approve, revise, reject, needs-a-person by rules that no model output can
    change: an unmet criterion is never an approval; an empty result is never an approval; after `max_revisions`
    the next decision is a person's; a result containing instruction-like text is a person's decision even when the
    Manager approved; a result containing something shaped like a credential is not sent to any model at all; an
    unreadable verdict or an unavailable model leaves the result with a person (or a retry); with the review policy
    `human` the Manager's verdict is recorded as advice only. A person may decide at any time.
11. **Critical tasks need the Approval Center.** For a task with a critical operation, a passed review only creates
    an approval whose frozen payload names the task, the operation, the submission, its version and its hash. Only
    that approval, decided by a person with `approvals.decide`, completes the task; an approval whose payload no
    longer matches the reviewed version is not honoured and is audited.
12. **Worker output is data.** It is never placed in a system prompt. Towards the Manager it is fenced with a random
    marker that is removed from the text if it occurs there; towards the next worker an approved result is passed as
    labelled untrusted data after the task, followed by a reminder of the rules.
13. **Secrets.** A prompt is redacted like every other input of the platform before it is stored or sent, and the
    response says so (`prompt_redacted`). Provider and bridge keys are encrypted, write-only and appear in no
    response, log, event or prompt. Account addresses are stored masked (ADR-032 §10).
14. **The report separates what is known from what is claimed.** Each task result is in exactly one class, computed
    from recorded rows: `verified` (a machine check of exactly the accepted version), `human_approved` (a person
    accepted it or approved its critical operation), `worker_proposal` (the Manager accepted it and every criterion
    is backed by a quote that really occurs in the text), `unverified_claim` (accepted on the Manager's judgement
    alone), `failed`, `incomplete`. "Facts" are sentences the platform derives from its own rows. The Manager's
    summary is appended as model text and cannot change a class. One report version per state of the objective.
    Anything that involved a mock provider, the fake bridge or a mock Manager — result, review, plan or summary — is
    labelled mock. An objective is `completed` only when every task is; otherwise it is `failed` and its report is
    `partial`, including when a person cancelled a task on purpose.
15. **Permissions.** `workforce.read`, `run`, `work`, `review`, `manage`, and `bridge` (ADR-032 §6). `manage` and
    `bridge` are human-only. Every other organization gets 404 for every workforce object.
**Consequences:** One prompt can be planned, executed by any mix of Arena sessions and people, reviewed and
consolidated, and it still works with no bridge at all. The price is a second scheduler beside the orchestrator's,
kept small by sharing everything below it.
**Limits that are part of the design:**
- Workers are text-only: nothing inside an Arena session can run tools, tests or code of the platform.
- The `verified` class is empty for text work. It needs a machine check, which only development tasks applied to a
  worktree can provide (WF-14, since built — ADR-034). For text tasks the strongest classes remain `human_approved`
  and `worker_proposal`, and the report says that nothing was checked by the platform.
- The review is as good as the Manager's model. The rules stop the known bad cases; they do not make a wrong
  answer right.
- A bridge delivers at-least-once. The platform never resends blindly, but a request whose outcome is unknown may
  have been answered in the Arena session; the person who takes the task over is told so.
**Not verified in development:** the Manager on a real Claude model (WF-21) and Automatic workers on a real Arena
Local Bridge with four accounts (WF-20). Every test used the mock provider as the Manager and the fake bridge.

**Addendum (2026-11-11) — two people on the critical path.** The approval of a critical task was first requested by
`workforce`, whoever did the work. A person who did a Human-Assisted task could therefore submit a result and then
approve it alone, which contradicts the platform rule that a requester never decides their own request. Now the
requester of that approval is the person who submitted the result (`user:<id>`), so the existing
`self_approval_forbidden` check applies; an Automatic worker's result is still requested by `workforce`. The evidence
of the approval names who submitted. Not changed: a person may review their own **non-critical** result when the
Manager hands it to a person — forbidding that would stop a one-person installation whenever the Manager escalates.
The history and the result show who submitted and who accepted. Tests: `test_workforce_review.py`
(`…only_by_a_human_approval…`, `…automatic_worker_is_requested_by_the_platform`), `test_workforce_security.py`,
`e2e/workforce.spec.ts`.

## ADR-034 — Development tasks from text workers: the platform applies, tests and merges (2026-11-11)
**Status:** Accepted. Completes WF-14. Builds on ADR-021 (worktrees, approved merges), ADR-019 (sandbox) and ADR-033.
Code: `backend/app/workforce/devtasks.py`, hooks in `review.py`, `scheduler.brief`, `manager.assign`.
**Context:** Workers behind Arena Local Bridge, and people pasting into Arena, can only return text. The report's
`verified` class needs a machine check of exactly the accepted version, so without a way to turn text into checked code
it was always empty, and the owner's picture of "a backend worker writes code and opens a change" did not exist.
**Decision:**
1. **Opt-in per workforce.** `workforces.settings.development = {enabled, test_command, timeout_seconds}`, changed only
   with `workforce.manage` (`PATCH /api/workforce/development`). The test command must pass the sandbox's `shell.test`
   allow-list; it is an argv list, never a shell string. While off, a plan that contains a `code` task is invalid and
   goes back to the Manager.
2. **The plan says which tasks are code.** `PlannedTask.kind` (`text` | `code`) and `files` (at most 12 repository
   paths to show the worker). Stored on the task as `result.kind` / `result.files`; no schema change.
3. **One delivery format, explained in the brief.** Complete files in fenced blocks ```` ```file:<path> ````, deletions
   as `delete:<path>`; a longer fence for files that contain ```` ``` ````. The brief adds the format, the test command
   and the repository listing plus the requested files from the base branch, fenced as data. It is built from the base
   branch only, so the brief and its hash are stable.
4. **The platform applies; the worker never touches git.** Each version: reset the task's worktree to the current tip
   of the base branch, write exactly this version's files through `worktrees.write_file` (relative paths only; no
   `..`, no `.git`, no symlinks), commit on `agent/<worker>/task-<id>` (protected branches refused), track conflicts,
   then run the test command in the Docker sandbox (no network, read-only root, limits).
5. **The platform's check is deterministic and comes first.** No blocks, an unsafe path, no change or failing tests:
   the result is sent back for revision with the platform's own output (and to a person after the revision limit). The
   Manager is not asked. Passed (or no command): the Manager reviews as for any result.
6. **Accepting code is a merge request, not a merge.** An accepted code result requests `git.merge_protected` for
   exactly the tested head commit, with the submission's hash in the frozen payload. The task stays *approved* until a
   person approves; the Approval Center's merge executor merges exactly that commit or refuses. The submitter of a
   person's result cannot approve it. A refused merge (base moved, conflict) sends the task back with the reason.
7. **`verified` is earned only by tests.** A `verification` evidence row is written only when tests ran; the report's
   `verified` class needs one that passed for the accepted submission. Without a test command the code can still be
   merged after approval, but it is classified by who accepted it, never `verified`.
**Consequences:** Text workers can deliver code, the owner sees the tested branch and approves the merge, and the
report can finally say *verified by the platform*. A code result costs a sandbox run per version.
**Limits:** the merge goes into the platform's local project repository; GitHub pull requests for workforce tasks are
not opened yet. Workers see at most 12 files plus a listing of 300 paths, so large refactors are out of reach. Tested
with the mock Manager, people's submissions, real git and the real sandbox; **not** with a real model writing code.


## ADR-035 — A worker can drive its own visible browser window (owner decision, 2026-11-12)
**Context:** On the owner's Ubuntu machine Arena Local Bridge kept one shared, hidden browser, signed in with stored
passwords and renewed the sign-in every few minutes; prompts were never submitted (`page.waitForResponse` timeouts)
and its pages closed after each turn, so nobody could watch or step in. The owner asked for one browser profile per
worker, signed in by hand, kept open for watching and manual intervention.
**Decision:** reverse the ADR-032 retirement *for this path only*. The built-in Browser Bridge site `arena`
(https://arena.ai/agent) is back, locked until its Arena-specific terms statement (sections 5(vi), 3.4) is
acknowledged. A worker gets a third connection besides an API provider and Arena Local Bridge: its own window —
`POST /api/workforce/workers/{id}/browser` opens (or reuses) a Browser Bridge session with the profile
`agent-<identifier>` (one profile per worker, one window per profile) and sets the worker's agent to the existing
`browser` engine. The platform never holds Arena passwords: the person signs in once in that window; the profile keeps
it. The window stays open after each task. Eligibility reads the window's state: not READY (signed out, a bot check,
a changed page, closed) → `session_unavailable` → the task goes to a person (the window is there to fix it); nothing
is typed. The engine reports `bridge_unavailable` (retry, nothing reached the window), `session_unavailable` (person)
or `outcome_unknown` after the prompt was sent (person — never typed twice). Custom sites on Arena's hosts stay
refused (`use_builtin_arena_site`) so the Arena terms statement cannot be bypassed. Arena Local Bridge stays available.
**Consequences:** visibility and manual control at the price of DOM selectors: the `arena` selectors are guesses,
**never checked against the live page**, and are adjustable under Browser Bridge → Selectors. The account behind a
window is a person's sign-in, so the binding says which profile and site, never which account (`attested: false`).
Arena's terms still prohibit automated access; the acknowledgement records that the owner accepts that risk.
**Tested:** backend with the real Browser Bridge (Playwright Chromium) on the platform's test chat page: a worker
opens its own window, drives it through the scheduler and runtime, the window stays open; a window that is not ready
hands the task to a person with nothing typed; failure categories (tests/test_workforce_browser.py). Browser E2E:
the worker dialog offers the window (ar/en). **Not** run against arena.ai.
