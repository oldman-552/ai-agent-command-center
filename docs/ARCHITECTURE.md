# Architecture

A modular monolith (ADR-001): one FastAPI codebase deployed as several processes, one PostgreSQL database, RabbitMQ
for work and events, Redis for ephemeral live data, and a Next.js web app. Decisions and their reasons are in
[ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md).

```
Browser ──HTTPS──▶ frontend (Next.js, :3000) ──/api/* + WebSocket──▶ api (FastAPI, :8000)
                                                                     │  │  │
                       ┌─────────────────────────────────────────────┘  │  └── Redis (live updates, heartbeats)
                       ▼                                                ▼
                 PostgreSQL 16 ◀──────── outbox relay ─────────▶ RabbitMQ (acc.commands / acc.events / retry / DLQ)
                       ▲                                                │
                       └──────────── worker (executions, orchestrator, supervisor, automations) ◀┘
                                         │            │                      │
                                  Docker sandbox   git worktrees      model gateway ──▶ Anthropic / OpenAI /
                                  (per command)    (per task)                          OpenAI-compatible / Gemini
                                                                       browser-bridge (experimental, Playwright)
```

## Processes

| Process | Command | Role |
|---|---|---|
| `api` | `uvicorn app.main:app` | REST API, WebSocket `/api/ws`, A2A server `/api/a2a`, MCP server `/api/mcp/`, outbox writes |
| `worker` | `python -m app.worker` | Outbox relay + consumers; roles (`WORKER_ROLES`): `executions`, `orchestrator`, `supervisor`, `automations`; heartbeat in Redis |
| `migrate` | `alembic upgrade head` | Runs once before api/worker start |
| `browser-bridge` | `uvicorn app.browser.server:app` | Owns the browsers of the experimental bridge (internal token, private network) |
| `frontend` | `node server.js` | Next.js standalone; proxies `/api/*` so the session cookie stays first-party |

Several workers can run; the supervisor and the automation scheduler elect one leader each with a PostgreSQL
advisory lock. Executions survive restarts: an orphaned execution of a dead runtime is marked TERMINATED and the
recovery workflow resumes it from its last checkpoint when that is safe (ADR-020).

## Backend packages (`backend/app`)

| Package | Responsibility |
|---|---|
| `security` | Login (JWT in HttpOnly cookie, CSRF double-submit), RBAC (6 roles, human-only permissions), tenancy scoping, encryption (Fernet), redaction, audit log |
| `control` | Agents (CRUD, versions, templates, lifecycle state machine), teams, users & roles |
| `runtime` | Execution contract, state machine, checkpoints, tool calls with permission checks, circuit breakers, dispatch (bus or inline), engines `mock` and `cli`; overview API |
| `connectivity` | Provider registry, model gateway (adapters, routing, usage and cost records), A2A v1.0 server/client and the `a2a` engine |
| `bus` | Envelope contract, topology, transactional outbox + relay, idempotent consumers (lock → ledger → handler → ack), retry tiers, DLQ |
| `orchestration` | LangGraph planner, agent selection, scheduler (dependencies, review, approvals), projects/workflows/tasks API, repository inspector |
| `evaluation` | Deterministic checks + optional reviewer model (can only withhold), performance profiles |
| `governance` | Approval Center (frozen payloads, executors), audit search/export, budgets |
| `sandbox` | One Docker container per command, argv policy, workspace resolution |
| `devplane` | Hardened git, per-task worktrees, protected branches, approved merges, GitHub client, PRs, webhooks |
| `supervisor` | Detection of stalled/runaway/failing work, closed list of reversible actions, leader loop |
| `memory`, `context` | Scoped memory store with hybrid retrieval; trust-separated, budgeted context assembly |
| `integrations` | MCP client/registry, platform MCP server, skill & server security assessment |
| `browser` | Experimental Browser Bridge (gate, adapters, Playwright sessions, service, `browser` engine) |
| `automation` | In-app scheduled/event automations and their history |
| `observability` | Dashboard and analytics aggregates, JSON logs, OpenTelemetry, live updates, health report |

## Data flow of an objective

1. `POST /api/workflows` stores the objective and enqueues `workflow.plan` in the same transaction (outbox).
2. The orchestrator consumer runs the LangGraph planner (draft → validate → persist) with the project's planning
   agent; the plan is schema-validated, cycle-checked and turned into tasks with dependencies.
3. `workflow.advance` dispatches every ready task to the best agent (roles, skills, quality, success, load, cost,
   latency, circuit state) as an execution with a trust-separated, budgeted context.
4. The execution runs on its engine (API model, CLI in the sandbox, A2A remote agent, browser session); tools run in
   the sandbox and the task's worktree; every event is stored and streamed.
5. The evaluation gate scores the result; failures go back for revision, passes go to human review, merge or
   critical-operation approvals; only approved results complete the task.
6. Audit entries and live updates are written for every step; budgets, circuits and the supervisor keep it in bounds.

## Frontend (`frontend/`)

Next.js App Router, next-intl (Arabic default, RTL), TanStack Query, Tailwind logical properties, Lucide icons,
self-hosted fonts. Every page has Playwright E2E tests in both languages with axe (WCAG 2.2 AA) and contrast checks.
The design system is in `design-system/ai-agent-command-center/MASTER.md`.
