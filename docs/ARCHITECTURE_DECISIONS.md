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
- Not yet: streaming, push notifications, extended card, multi-turn follow-ups — advertised `false` and answered with
  the spec's error codes.
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
