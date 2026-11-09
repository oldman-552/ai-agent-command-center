# Implementation Plan — Spec v4.0

Spec: [`MASTER_PROMPT.md`](MASTER_PROMPT.md) (v4.0). Previous spec and plan: `MASTER_PROMPT.v1.md`, `IMPLEMENTATION_PLAN.v1.md`.
Requirements matrix (115 requirements, IDs, acceptance, verification): [`REQUIREMENTS.md`](REQUIREMENTS.md), source `requirements.json`.
Decisions: [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md). Resuming work: [`HANDOFF.md`](HANDOFF.md).

## Milestone weights (total 100%)

| M | Milestone | Weight |
|---|---|---:|
| 1 | Foundation | 8 |
| 2 | Universal Agent Runtime | 9 |
| 3 | Dynamic Agent Management | 7 |
| 4 | Multi-Provider AI Gateway | 8 |
| 5 | Execution Engines | 6 |
| 6 | Intelligent Orchestration | 9 |
| 7 | Agent Supervisor | 6 |
| 8 | GitHub Development | 7 |
| 9 | Browser Bridge and MCP | 7 |
| 10 | A2A and Communication | 5 |
| 11 | Context and Memory | 7 |
| 12 | Evaluation and Recovery | 6 |
| 13 | Enterprise Security | 7 |
| 14 | Enterprise Experience | 5 |
| 15 | Final Integration | 3 |

Milestone % = verified requirements / requirements. A requirement is verified only with evidence (passing test,
command output). `scripts/progress.py` computes everything; `progress.txt` and `PROJECT_STATUS.json` are generated.

## Build order (dependency-driven, not milestone number)

1. **Security core** — RBAC + roles (SEC-01/02), orgs/teams (FND-15), CSRF (SEC-07). Everything else authorizes through it.
2. **Agents** — templates, CRUD, lifecycle, config versions (M3) + UI.
3. **Runtime contract** — execution model, state machine, events, cancel/timeout, checkpoints, tool permissions (M2).
4. **Gateway** — adapters (Anthropic, OpenAI, Google, OpenAI-compatible, Mock), models, credentials, usage/cost, fallback (M4) + UI.
5. **Bus** — RabbitMQ topology, message contract, idempotent consumers, recovery (M10 MQ-*).
6. **API engine + worker** — executions run end-to-end with Mock (EE-01).
7. **Orchestration** — LangGraph planner → DAG → parallel dispatch → review → approval → persistence (M6) + Task Center UI.
8. **Approvals + sandbox + audit** (M13).
9. **Evaluation + recovery** (M12), then **Supervisor** (M7).
10. **GitHub + worktrees** (M8).
11. **Context + memory** (M11).
12. **MCP, skills registry, Browser Bridge** (M9), **A2A** (M10), **CLI engine** (EE-02).
13. **Experience** — remaining pages, realtime, timeline, analytics, observability, automation (M14).
14. **Final integration** — scripts (Windows/Linux/macOS), prod compose, 21 docs, DoD E2E (M15).

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| No real provider key in dev env | Can't verify live calls (GW-10, FI-06) | Mock + HTTP-mocked adapter tests; live check documented as owner step |
| Container registries blocked in dev env | Official images unverified here | Offline equivalent images (ADR-010); owner verifies |
| Consumer AI terms vs Browser Bridge | Account suspension | Disabled by default + terms gate (ADR-005) |
| Prompt injection via repo/web content | Unsafe tool use | Trust-segmented context (CX-02), default-deny tools, sandbox, approvals |
| Scope size vs session limits | Partial delivery | Requirements matrix + HANDOFF.md; verified-only progress |
| Windows-only behaviours | Scripts untested here | Static checks + owner run (FI-01) |

## Testing strategy
- Backend: pytest against real PostgreSQL + RabbitMQ (no SQLite substitutes); HTTP-level mocks for external APIs.
- Frontend: Playwright in real Chrome, axe-core WCAG 2.2 AA, i18n guard, contrast check, tsc, next build.
- Stack: docker compose (offline images here, official on owner machines).
- Every requirement's verification method is listed in the matrix.
