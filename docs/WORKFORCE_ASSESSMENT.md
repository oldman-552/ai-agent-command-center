# AI Workforce — Architecture Assessment (Phase 1)

Date: 2026-11-11. Scope: turning this platform into **one prompt → Claude Manager → four specialised workers on four
Arena accounts through Arena Local Bridge → review → one consolidated result**.

Everything below comes from reading the actual source of both repositories. Nothing was assumed from a README.

| Repository | Revision inspected | How |
|---|---|---|
| `oldman-552/ai-agent-command-center` | `d953576` (main) | full read of the packages listed in §2 |
| `Gandhara2077/arena-local-bridge` (ALB) | v1.1.0, `403122c` | source read only — **never executed, no Arena account used** |

> **Verification level of this document:** static inspection. Statements about Arena Local Bridge describe what its
> code does. They were not confirmed against a running bridge or a real Arena account (see §9).

---

## 1. What Arena Local Bridge really is

A Node ≥ 20 program (MIT) that drives **arena.ai Agent Mode through a signed-in Chrome/Edge** with Playwright and
exposes the result as a local HTTP service. It is not an official Arena API.

### 1.1 Interface (from `src/server.mjs`)

| Route | Auth | Behaviour |
|---|---|---|
| `POST /v1/chat/completions` | `Authorization: Bearer <ARENA_AGENT_BRIDGE_KEY>` | OpenAI-compatible. Stream (SSE) and non-stream. |
| `GET /v1/models` | Bearer | Archived sessions (`owned_by: arena-session`, id = session UUID), or `agent`; always `ping`. |
| `GET /health` | none (loopback) | Always 200. `ok, ready, version, accounts[], queue{active,depth,maxDepth}, stats{…}`. |
| `GET /ready` | none (loopback) | 503 until an account has been verified usable. |
| `GET /api/sessions` | none (loopback) | Archived sessions with their owning account `email`, model, title. |
| `GET /api/status` | none (loopback) | **Returns the bridge key.** The platform never calls it. |
| `GET /metrics`, `/api/pool/*`, `/api/account/quota`, … | Bearer | Bridge administration; not used by the platform. |

Defaults: `HOST=127.0.0.1`, `PORT=20140`. The bridge refuses to start without `ARENA_AGENT_BRIDGE_KEY`.

### 1.2 Facts that shape the design

1. **Host guard.** Every request whose `Host` header is not `127.0.0.1`, `localhost` or `::1` is answered
   `403 "This server only answers requests from this machine."` — for every route and regardless of `HOST`. It is a
   DNS-rebinding defence for browsers. A container that calls `http://host.docker.internal:20140` therefore gets 403
   unless the client sends `Host: 127.0.0.1:20140`.
2. **`model` selects the conversation, not an LLM.**
   - a session UUID → converse with that existing Arena session (never creates one);
   - `active` / omitted → the session selected in the bridge GUI (409 `no_active_session` when none);
   - `ping` / `health` → instant `pong`, no browser, no account involved;
   - anything else (e.g. `agent`) → legacy flow that may create a session; the source notes that session creation is
     commonly stopped by reCAPTCHA.
3. **Accounts.** The bridge keeps an encrypted pool. A session is always driven by the account that owns it
   (`409 session_account_unavailable` if that account is disabled); new sessions use the pool's primary account with
   fail-over. **No request field selects an account and no response says which account answered** — the only
   response metadata is `X-Arena-Bridge-Request-Id` and `X-Arena-Bridge-Version`.
4. **One turn at a time.** All turns of one bridge process pass through one serialized queue
   (`ARENA_MAX_QUEUE`=8 → `503 bridge_queue_full`, `Retry-After: 10`; rate limit → `429 rate_limited`).
5. **System messages are ignored on the session path.** Only the last user message is sent to Arena.
6. **Usage is always zero** (`prompt_tokens: 0 …`). It is not a measurement.
7. **No server-side timeout.** A turn can take many minutes; streaming sends an empty-delta heartbeat every 10 s.
8. **Idempotency is best effort.** An identical in-flight request is joined. A finished result is replayed for
   120 s (`ARENA_RESULT_CACHE_TTL_MS`) only when the caller sends `x-arena-idempotency-key`. The source states that
   a send with an unknown outcome can still be delivered twice.
9. **Errors:** `{"error":{"message","type":"arena_agent_error"|"invalid_request_error"|"rate_limited","code"?,
   "request_id"}}`, default status 502; after the SSE stream is open, the error arrives as a `data:` frame.

### 1.3 What this means for "four accounts, four workers"

The bridge has **no deterministic account selector**. A worker can still be tied to one account in two honest ways:

| Binding | How | Account certainty |
|---|---|---|
| **Pinned session** | The worker's model is the UUID of an Arena session owned by that account. | Deterministic by bridge rule (owner is never substituted). Readable from `/api/sessions`. Not attested per response. |
| **Dedicated bridge instance** | One bridge process per account (own data directory, port and key). | Deterministic by construction (one enabled account). Also gives real parallelism. *Inferred from the source — the multi-instance setup was not run.* |
| **Pooled** | Model `agent`/`active` on a bridge with several enabled accounts. | **Not fixed**: primary account, with fail-over. Shown as *pooled* in the UI. |

---

## 2. Existing platform: what was found

A FastAPI modular monolith (`backend/app`), PostgreSQL + Alembic, RabbitMQ through a transactional outbox, Redis for
live updates, LangGraph for planning, Next.js frontend, Docker Compose, Windows launchers. Recorded baseline:
backend 327 tests, E2E 66.

### 2.1 Reusable as is

| Capability | Where | Why it fits |
|---|---|---|
| Provider registry, encrypted write-only keys, test/discover endpoints | `connectivity/providers.py`, `models/connectivity.py` | A bridge instance is a provider with a base URL and a secret. |
| Adapter contract (`stream`, `list_models`, `test_connection`), error classes | `connectivity/gateway/base.py` | The ALB adapter implements the same protocol. |
| API execution engine: fallback, circuit breaker, usage records, checkpoints | `connectivity/gateway/service.py` | Automatic workers run through it. |
| Execution runtime: state machine, deadlines, cancel, retry with backoff, resume | `runtime/service.py`, `runtime/contract.py` | Timeouts and retries per worker; restart recovery. |
| `InputRequired` → `WAITING_FOR_INPUT` parking and resume | `runtime/service.py` | The mechanism behind Human-Assisted work. |
| Planner as a LangGraph graph with PostgreSQL checkpoints | `orchestration/planner.py` | The Manager's planning stage; already resumable. |
| Stateless scheduler: dependencies, fan-out/fan-in, idempotent `advance()` | `orchestration/scheduler.py` | Tracking survives restarts; no duplicate dispatch. |
| Capability matching and scoring | `orchestration/selection.py` | Worker selection and redistribution. |
| Evaluation gate (deterministic checks first) | `evaluation/engine.py` | "Never accept on HTTP 200". |
| Approval Center with frozen, hashed payloads | `governance/approvals.py` | Protected merges and critical tasks. |
| Trust-separated context assembly, untrusted-data fencing | `context/assembler.py` | Prompt-injection defence for worker output. |
| Outbox, retry tiers, DLQ, idempotent consumers | `bus/*` | Queue failure and redelivery handling. |
| Worktrees, changed files, conflicts, GitHub PRs | `devplane/*` | Development tasks. |
| RBAC, tenancy helper, audit log, redaction | `security/*` | New routes use the same guards. |
| OpenTelemetry tracing, live updates | `observability/*` | Trace IDs across the new flow. |

### 2.2 Must be modified

| Area | Change |
|---|---|
| `Task` | Add objective, instructions, expected output, timeout, execution mode, assignment source, lifecycle. |
| Task lifecycle | The 10 coarse statuses stay (scheduler, UI and tests rely on them). A persisted fine-grained lifecycle is added and every transition goes through one function that keeps both consistent and writes history. |
| Planner | Workforce objectives get an analysis step, worker assignment and richer task fields. |
| Scheduler | Honour planned/manual assignment, redistribute when a worker is disabled, per-attempt engine (automatic or human), Manager review, escalation, fallback to Human-Assisted, consolidation. |
| Gateway | Request metadata (session, idempotency key), provider-request IDs, "usage unknown", providers without tool support. |
| Usage accounting | A usage record must be able to say "not reported by the provider" instead of 0. |
| Engine enum, bus topology | New `human` engine and its work queue. |
| RBAC | `workforce.*` permissions. |
| Health report, compose, launchers | Arena Local Bridge reachability; `host.docker.internal`. |

### 2.3 Must be removed or migrated (the old Arena path)

The repository contains one Arena path: the **built-in `arena` site of the Playwright Browser Bridge**
(`backend/app/browser/adapters.py`), with guessed selectors, the Arena terms statement, per-person profiles signed in
to Arena, and matching UI text, documentation and tests (ADR-031).

Dependency analysis of `backend/app/browser/` (10 importers checked: `main.py`, `worker.py`, `runtime/contract`,
`observability/realtime.py`, `integrations/mcp_server.py`, `bus/topology.py` via the engine enum, tests, UI):

| Item | Arena-specific? | Decision |
|---|---|---|
| `BUILTIN["arena"]` adapter, selectors, `terms_warning` | yes | **Remove.** |
| Arena as a *custom* site (any `arena.ai` / `lmarena.ai` host) | yes | **Refuse** at the gate, in the catalogue and in the bridge service. |
| Stored acknowledgements / selector overrides / sessions for `arena` | yes | **Migrate**: archived under `retired_arena`, sessions stopped; reversible. |
| Arena wording in UI, messages, docs, E2E and backend tests | yes | **Rewrite / remove.** |
| Bridge service, gate, profiles, custom sites, test page, desktop mode | no — generic | **Keep** (general-purpose browser tools). |
| `browser` execution engine, MCP browser tools, `browser_sessions` table | no — generic | **Keep.** |
| `browser_bridge.manage` permission, `/api/browser-bridge/*`, health entry | no — generic | **Keep.** |

The bridge directory is therefore not deleted; only its Arena-specific content is.

### 2.4 Missing

Workforce and worker entities; the Arena Local Bridge adapter, settings and diagnostics; Human-Assisted task cards,
acceptance, submission, attachments and resubmission; Manager review and consolidation; result versioning; task
transition history; the AI Workforce UI section; `INSTALL.bat`, `RESET.bat`; bridge checks in the health tools;
tests for all of it.

---

## 3. External dependencies

| Dependency | Needed for | Available in the development sandbox |
|---|---|---|
| Arena Local Bridge running on the Windows host | automatic workers | **No** — cannot run (needs Arena accounts and a desktop browser) |
| Four Arena accounts signed in to the bridge | four-account operation | **No** |
| A Claude model for the Manager (Anthropic API key or another configured provider) | real planning/review | **No** key, no outbound access |
| Docker Desktop on Windows 10/11 | deployment, `host.docker.internal` | **No** Docker here |
| PostgreSQL 16, RabbitMQ, Redis | tests | Yes (built locally: PostgreSQL 16.15, RabbitMQ 3.12.14 on Erlang 26, Redis 7.2) |
| Chromium | E2E | Yes (headless build from npm) |

---

## 4. Security concerns

| Concern | Handling |
|---|---|
| Arena's terms. The repository's own notes cite a clause against automated access; the bridge automates the web app. | The integration is **off until a human with a human-only permission acknowledges an explicit statement**; audited; revocable. Human-Assisted mode needs no bridge. The platform adds no evasion of its own and does not use or recommend the bridge's reCAPTCHA broker or proxy helper. |
| Bridge key | Stored encrypted, write-only, shown as a 4-character hint. Never logged; redacted from errors. `/api/status` (which returns the key) is never called. |
| Host header | The adapter sends `Host: 127.0.0.1:<port>` so the bridge can stay bound to loopback. This is the only way a container can reach it; the Bearer key is still required. Visible and configurable per bridge; recorded in ADR-032. |
| SSRF through a configurable bridge URL | Only `http(s)` URLs whose host is loopback, `host.docker.internal`, or a private address; no redirects followed; no credentials in the URL. |
| Account emails returned by the bridge | Masked before storage and display; a salted hash is kept only to tell accounts apart. |
| Worker output is untrusted | Fenced as untrusted data for the Manager and for dependent tasks; size-capped; schema-validated where JSON is expected. |
| Human submissions | Size and type limits on attachments, stored outside the web root, served only to authorised users of the same organisation. |
| Self-approval | A worker's agent can never be the reviewer of its own task; protected merges and critical tasks still need the Approval Center. |
| Exposure | The bridge is never published; compose adds no port for it. |

---

## 5. Limitations that follow from the bridge (not fixable in the platform)

1. Account identity is **configured, not attested**: no response carries the account.
2. One bridge instance is **sequential**; four workers on one instance do not run in parallel.
3. **Token usage is unavailable** through the bridge.
4. **Exactly-once delivery is not available**; the platform narrows the window (stable idempotency key, no blind
   resend after the replay window) but cannot close it.
5. The bridge depends on Arena's web application and can break when Arena changes it.
6. Platform tools (repository, shell, git) are **not offered to Arena sessions**; workers through the bridge exchange
   text.

---

## 6. Implementation sequence

| Phase | Content | Verifiable outcome |
|---|---|---|
| 1 | This assessment, ADR-032 | documents |
| 2 | Remove the Arena path from the Browser Bridge; migration; tests | no code path reaches Arena through Playwright |
| 3 | Arena Local Bridge adapter, settings, diagnostics, health; faithful fake bridge | adapter tests against the fake over real HTTP |
| 4 | Workforce, four workers, modes, bindings | four-worker configuration tests |
| 5 | Claude Manager: analyse → plan → assign → review → consolidate | objective → tasks → report |
| 6 | Human-Assisted engine, mixed modes, fallback | mixed-mode end-to-end test |
| 7 | AI Workforce UI | E2E + accessibility |
| 8 | Failure recovery and security tests | bridge down, invalid reply, restart, DLQ |
| 9 | Windows scripts, compose | script review + static checks (not runnable here) |
| 10 | Full validation, documentation, report | exact test counts |

---

## 7. Test tiers (kept separate in the code and in every report)

| Tier | Marker | Uses |
|---|---|---|
| Mock | default | built-in Mock provider, fake bridge |
| Local integration | default | real PostgreSQL, RabbitMQ, Redis, real HTTP to the fake bridge |
| Real provider | `real_provider` (skipped unless a key is supplied) | Anthropic/OpenAI API |
| Real Arena | `real_arena` (skipped unless a bridge URL and key are supplied) | a running Arena Local Bridge |

---

## 8. Assumptions recorded

- Workers through the bridge are text-in/text-out; code changes from such workers are applied by the platform into a
  worktree, never by the worker.
- The default mode of every new worker is **Human-Assisted**, so a fresh installation works without the bridge and
  nothing automates Arena before the owner configures and acknowledges it.
- "Claude Manager" means the Manager agent runs on a Claude model through a configured provider; the provider and
  model stay configurable.
- The suggested lifecycle is extended by one state, `READY` (dependencies satisfied, waiting for an eligible worker).

## 9. NOT VERIFIED (needs the owner's machine)

- Any request to a real Arena Local Bridge, a real Arena account, or four accounts at once.
- That `host.docker.internal:20140` reaches a bridge bound to `127.0.0.1` on Docker Desktop for Windows.
- Running one bridge instance per account.
- A real Claude model as Manager.
- The Windows scripts and the Docker images.
