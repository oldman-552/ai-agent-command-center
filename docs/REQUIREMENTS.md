# Requirements Matrix

Generated from `docs/requirements.json` by `scripts/progress.py`. Spec: `docs/MASTER_PROMPT.md` v4.0.

**Overall verified completion: 25.1%**

## M1 — Foundation (weight 8%, 94%) — spec Phases 1, 19, 23, 25

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| FND-01 | P0 | Monorepo structure backend/frontend/docs/scripts | Dirs exist with build files | inspection | — | done | backend/, frontend/, docs/, scripts/, design-system/ in repo |
| FND-02 | P0 | FastAPI backend with settings from env + health | GET /api/health 200 with DB check | pytest test_health | — | done | GET /api/health tested (test_health); pydantic-settings from env |
| FND-03 | P0 | PostgreSQL schema via SQLAlchemy + Alembic | upgrade/downgrade/upgrade on clean DB | alembic + pytest builds schema via migration | — | done | v4 baseline migration 40 tables up/down/up; tests build schema via migration |
| FND-04 | P0 | Authentication: argon2id, JWT HttpOnly cookie, rate limit, logout-all | Auth tests pass | pytest | FND-03 | done | login/logout/logout-all/me, argon2id, JWT cookie, rate limit — 6 tests pass |
| FND-05 | P0 | Super Admin bootstrap from env, no hardcoded credentials | Idempotent bootstrap test | pytest | FND-04 | done | bootstrap from ADMIN_USERNAME/ADMIN_PASSWORD, idempotent — tests pass |
| FND-06 | P0 | Encrypted credential storage + secret hints + log redaction | Roundtrip + redaction tests | pytest | — | done | tests: encryption roundtrip, hint, redaction (test_foundation.py) |
| FND-07 | P0 | Next.js bilingual shell: ar default RTL, en LTR, persisted switcher | E2E lang/dir/persistence | Playwright | — | done | switcher in login + app shell; cookie + user profile; persistence E2E tests pass |
| FND-08 | P0 | No hardcoded UI strings; ar/en key parity | check-i18n passes | script | FND-07 | done | messages/ar.json + en.json; scripts/check-i18n.mjs passes and catches hardcoded text |
| FND-09 | P0 | Dashboard with live DB-derived data | Backend test asserts DB counts; E2E shows data | pytest + Playwright | FND-07 | done | dashboard reads /api/dashboard/summary; backend test asserts DB-derived counts, mock usage excluded |
| FND-10 | P0 | Design system from UI UX Pro Max skill (installed) | Skill installed; MASTER.md; tokens | inspection | — | done | official skill v2.15.0 (MIT) installed at .claude/skills/ui-ux-pro-max (marketplace layout); script execution blocked here, documented |
| FND-11 | P0 | WCAG 2.2 AA: contrast + axe on all pages, ar/en, light/dark | check-contrast + axe pass | script + Playwright | FND-10 | done | check-contrast 40/40; axe WCAG 2.2 AA 8/8 (login, dashboard × ar/en × light/dark) |
| FND-12 | P0 | Responsive 375/768/1024/1440 without horizontal overflow | E2E overflow checks pass | Playwright | FND-07 | done | Playwright overflow checks 8/8 at 375/768/1024/1440 ar/en |
| FND-13 | P0 | docker-compose stack: healthchecks, migrate job, persistent volumes | Stack healthy; data survives stop/start | docker compose (offline images in dev env) | — | done | compose config valid; offline-image stack: 6 services healthy, data persists stop/start |
| FND-14 | P1 | Redis service + health | Redis reachable, health endpoint reports it | pytest + compose | FND-13 | pending | — |
| FND-15 | P1 | Organizations/teams schema for multi-tenant readiness; tenant scoping in authz | Every project-scoped query filtered by org; test cross-org denial | pytest | FND-03 | done | organizations/teams + organization_id on scoped tables, tenancy.scoped/get_scoped; cross-org test |
| FND-16 | P1 | Requirements matrix, decision log (ADRs), progress.txt + PROJECT_STATUS.json | Files generated from requirements.json | inspection | — | done | docs/requirements.json (115 reqs), REQUIREMENTS.md, ARCHITECTURE_DECISIONS.md (11 ADRs), progress.txt + PROJECT_STATUS.json generated |

## M2 — Universal Agent Runtime (weight 9%, 100%) — spec Phase 2

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| RT-01 | P0 | Provider-independent runtime interface (ExecutionEngine adapter contract) | Abstract contract + registry; no provider imports in core | pytest + import-lint | — | done | runtime/contract.py ExecutionEngine + registry; AST test proves no provider/LangGraph imports |
| RT-02 | P0 | Execution record with full contract fields (ids, provider, model, engine, context, permissions, timestamps, status, output, usage, cost, logs, errors, evidence, checkpoints) | Model + migration + API | pytest | RT-01 | done | executions table with full contract fields; test_successful_run_records_full_contract |
| RT-03 | P0 | 13-state execution state machine with recorded transitions; illegal transitions rejected | State machine tests | pytest | RT-02 | done | 13-state TRANSITIONS, state events gapless, illegal rejected (tests) |
| RT-04 | P0 | Streaming output events (WebSocket) for executions | Client receives ordered events | pytest + Playwright | RT-02 | done | WebSocket /api/executions/{id}/stream ordered events + end; cookie auth, same-origin check (tests) |
| RT-05 | P0 | Cancellation and timeout handling | Cancel/timeout tests end in CANCELLED/FAILED with reason | pytest | RT-03 | done | cooperative cancel (running + idle), deadline via asyncio.timeout (tests) |
| RT-06 | P0 | Checkpointing and resume from last checkpoint | Kill mid-run, resume continues | pytest | RT-03 | done | checkpoint per step, TERMINATED orphans resume from latest checkpoint (test_resume_from_checkpoint_after_runtime_crash) |
| RT-07 | P0 | Tool registry + per-agent tool permission validation (default deny) | Unpermitted tool call blocked + audited | pytest | RT-01 | done | default-deny tool gate, audited denial, WAITING_FOR_TOOL state (test) |
| RT-08 | P1 | Retry policy with exponential backoff + retry budget | Retry tests | pytest | RT-03 | done | exponential backoff, retry budget, fatal not retried, resume without repeating steps (tests) |

## M3 — Dynamic Agent Management (weight 7%, 86%) — spec Phase 3

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| AG-01 | P0 | Agent CRUD with all 23 configuration fields | API tests for create/edit/read/list | pytest | FND-03 | done | POST/GET/PATCH /api/agents with all 23 config fields; test_create_with_all_fields_and_read_back |
| AG-02 | P0 | Clone, enable, disable, archive, delete | API tests | pytest | AG-01 | done | clone/enable/disable/start/stop/archive/restore/delete(archived only); test_clone_enable_disable_start_stop_archive_delete |
| AG-03 | P0 | 18 customizable agent templates (DB-stored, editable) | Seeded + CRUD tests | pytest | AG-01 | done | 18 system templates seeded, editable, custom templates, instantiate; test_templates_18_customizable_and_instantiate |
| AG-04 | P0 | Agent lifecycle states with recorded transitions | Transition history persisted | pytest | AG-01 | done | 13-state lifecycle with allowed-transition map, every transition recorded; illegal rejected (test) |
| AG-05 | P0 | Configuration versioning (snapshot per change) | Version increments on edit | pytest | AG-01 | done | agent_config_versions snapshot per change; test_edit_creates_config_version |
| AG-06 | P0 | Agents UI: list, create/edit form, details, templates — ar/en | E2E create/edit flow | Playwright | AG-01, FND-07 | done | Agents list/filters/actions, create/edit form (RHF+Zod, tool ceilings), details (versions, transitions), templates gallery; E2E CRUD flow + axe ar/en |
| AG-07 | P1 | Agent teams | Team CRUD + membership | pytest | AG-01 | pending | — |

## M4 — Multi-Provider AI Gateway (weight 8%, 0%) — spec Phases 9, 20

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| GW-01 | P0 | Provider adapter interface + registry (Anthropic, OpenAI, Google, Mock) | Adapters implement contract; Mock clearly labeled | pytest | RT-01 | in_progress | — |
| GW-02 | P0 | Provider + model registry with metadata/capabilities and pricing | CRUD + model list | pytest | GW-01 | pending | — |
| GW-03 | P0 | Encrypted API credentials, never returned in full, never in prompts | Masking tests | pytest | FND-06 | pending | — |
| GW-04 | P0 | Connection test per provider | Mock passes; real adapters tested with HTTP mocks | pytest | GW-01 | pending | — |
| GW-05 | P0 | Token + cost accounting per execution (usage_records/cost_records); no fabricated prices | Recorded per call; unknown price = null | pytest | GW-01 | pending | — |
| GW-06 | P0 | Fallback provider/model on failure | Primary failure → fallback used + logged | pytest | GW-01 | pending | — |
| GW-07 | P1 | Routing by complexity/capability/cost/availability | Routing tests | pytest | GW-02 | pending | — |
| GW-08 | P1 | Budgets: agent/project/task/daily/monthly; stop new work at limit + approval request | Budget tests | pytest | GW-05 | pending | — |
| GW-09 | P0 | Providers & Models UI | E2E add provider, test connection | Playwright | GW-02 | pending | — |
| GW-10 | P0 | At least one real official API call verified | Live call with owner key succeeds | manual/live test | GW-01 | pending | — |

## M5 — Execution Engines (weight 6%, 0%) — spec Phase 10

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| EE-01 | P0 | API execution engine (via gateway) | Runs task with Mock end-to-end | pytest | RT-01, GW-01 | pending | — |
| EE-02 | P1 | CLI execution engine adapter (sandboxed subprocess) with documented support for Claude Code / Codex / Gemini CLI / OpenCode | Adapter + docs; verified flags only | pytest + docs | RT-01, SEC-04 | pending | — |
| EE-03 | P1 | External agent runtime adapter (A2A client) | Adapter contract test | pytest | A2A-02 | pending | — |
| EE-04 | P0 | Unsupported integrations explicitly documented | PROVIDER_INTEGRATION.md table | inspection | — | pending | — |

## M6 — Intelligent Orchestration (weight 9%, 0%) — spec Phases 4, 13

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| OR-01 | P0 | Objective → plan → task DAG (LangGraph) | Plan creates tasks + dependencies (mock provider) | pytest | RT-01, AG-01 | pending | — |
| OR-02 | P0 | Agent selection by skills, permissions, workload, availability, cost, history | Selection tests | pytest | OR-01 | pending | — |
| OR-03 | P0 | Parallel fan-out/fan-in of independent tasks; dependency-driven scheduling | Independent tasks run concurrently | pytest | MQ-02 | pending | — |
| OR-04 | P0 | Review loop and human approval stage | Review requests revision; approval stage blocks | pytest | OR-01, SEC-03 | pending | — |
| OR-05 | P0 | Workflow persistence and resume after restart | Restart mid-workflow resumes | pytest | OR-01 | pending | — |
| OR-06 | P0 | Duplicate execution prevention | Same idempotency key never runs twice | pytest | MQ-03 | pending | — |
| OR-07 | P0 | Task center: Kanban (9 columns) + list + details with acceptance criteria, evidence, history | E2E | Playwright | OR-01 | pending | — |
| OR-08 | P0 | Tasks never marked complete on agent claim alone (evidence gate) | Completion requires passing evaluation | pytest | EV-01 | pending | — |
| OR-09 | P1 | Project modes: new / existing (repo inspection, tech detection) / maintenance | Existing-repo analysis produces map | pytest | GH-02 | pending | — |

## M7 — Agent Supervisor (weight 6%, 0%) — spec Phase 5

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| SV-01 | P0 | Independent supervisor service monitoring agent/execution states | Separate module/worker | pytest | RT-03 | pending | — |
| SV-02 | P0 | Detect stalled, repeatedly failing, runaway-cost, excessive-retry executions | Detection tests | pytest | SV-01 | pending | — |
| SV-03 | P0 | Policy-authorized reversible actions: retry, reassign, stop runaway, isolate agent, fallback model | Action tests + audit | pytest | SV-02 | pending | — |
| SV-04 | P1 | Detect deadlocks, duplicate work, conflicting changes | Detection tests | pytest | SV-01 | pending | — |
| SV-05 | P0 | Supervisor never overrides security/approval policies | Test: approval-gated op not executed by supervisor | pytest | SEC-03 | pending | — |
| SV-06 | P0 | Supervisor UI: health, findings, actions log | E2E | Playwright | SV-01 | pending | — |

## M8 — GitHub Development (weight 7%, 0%) — spec Phase 12

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| GH-01 | P0 | GitHub auth (fine-grained token or GitHub App) stored encrypted | Credential tests | pytest | GW-03 | pending | — |
| GH-02 | P0 | Repos, branches, commits, PRs, diffs via API | Tests with HTTP mocks | pytest | GH-01 | pending | — |
| GH-03 | P0 | Per-agent git worktree + feature branch; track files/ownership/conflicts | Real git worktree tests | pytest | — | pending | — |
| GH-04 | P0 | Protected branches cannot be modified by agents | Push to main rejected by policy | pytest | GH-03, SEC-01 | pending | — |
| GH-05 | P0 | PR creation with description + test results | PR payload test | pytest | GH-02 | pending | — |
| GH-06 | P0 | Merge only via approved approval request | Unapproved merge blocked | pytest | SEC-03 | pending | — |
| GH-07 | P1 | Webhooks + CI check status | Webhook signature verified | pytest | GH-02 | pending | — |
| GH-08 | P0 | GitHub UI page | E2E | Playwright | GH-02 | pending | — |

## M9 — Browser Bridge and MCP (weight 7%, 0%) — spec Phases 11, 18

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| BB-01 | P0 | Reference evaluation of ai-browser-bridge (license, platform, security, ToS) | Report in docs | inspection | — | pending | — |
| BB-02 | P0 | Browser Bridge service: isolated profiles, 9 session states, Playwright, DISABLED by default + EXPERIMENTAL label | State machine tests against local test page | pytest/Playwright | BB-01 | pending | — |
| BB-03 | P0 | Per-provider ToS gate: provider adapter cannot be enabled until owner acknowledges terms | Gate test | pytest | BB-02 | pending | — |
| BB-04 | P0 | Never expose cookies/secrets; no CAPTCHA bypass; dedicated profiles only | Security tests | pytest | BB-02 | pending | — |
| MCP-01 | P0 | MCP client: register servers, discover tools/resources, health, permission enforcement | Test against local MCP server | pytest | RT-07 | pending | — |
| MCP-02 | P0 | MCP server exposing platform tools (incl. Browser Bridge tools) | MCP client can list/call tools | pytest | MCP-01 | pending | — |
| MCP-03 | P0 | Skills & MCP registry UI with security assessment before enable | E2E | Playwright | MCP-01 | pending | — |
| MCP-04 | P1 | Skill install with source inspection (dangerous commands, network, fs, credentials) | Scanner tests | pytest | — | pending | — |

## M10 — A2A and Communication (weight 5%, 0%) — spec Phase 6

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| MQ-01 | P0 | RabbitMQ topology: exchanges by event type, queues by specialty/priority, DLQ | Topology declared; test | pytest (live broker) | — | pending | — |
| MQ-02 | P0 | Structured message contract (ids, correlation, schema version) + ack + retries + timeouts | Contract + consumer tests | pytest (live broker) | MQ-01 | pending | — |
| MQ-03 | P0 | Idempotent consumers (dedupe by idempotency key) | Duplicate delivery processed once | pytest | MQ-02 | pending | — |
| MQ-04 | P0 | Recovery after broker/worker restart | Restart test | pytest/compose | MQ-02 | pending | — |
| A2A-01 | P1 | A2A agent card + task endpoint for platform agents | Spec-compatible JSON | pytest | — | pending | — |
| A2A-02 | P1 | A2A client for external agent services | Client test against local server | pytest | A2A-01 | pending | — |

## M11 — Context and Memory (weight 7%, 0%) — spec Phases 7, 8

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| CX-01 | P0 | Context assembler with per-task-type profiles and token budget | Assembles within budget; tests | pytest | RT-01 | pending | — |
| CX-02 | P0 | Trust separation: system/policy/requirements/user/retrieved/tool-output segments; retrieved content cannot override policy | Injection tests | pytest | CX-01 | pending | — |
| CX-03 | P0 | Context dedup, prioritization, compression/summarization | Tests | pytest | CX-01 | pending | — |
| MEM-01 | P0 | Scoped memory store (9 types) with provenance, verified vs assumption flag, versioning, expiry | CRUD + scope isolation tests | pytest | FND-03 | pending | — |
| MEM-02 | P0 | Semantic retrieval | Relevant memories ranked first | pytest | MEM-01 | pending | — |
| MEM-03 | P0 | Project isolation of memory | Cross-project retrieval denied | pytest | MEM-01 | pending | — |
| MEM-04 | P0 | Context & Memory UI with human correction/deletion | E2E | Playwright | MEM-01 | pending | — |

## M12 — Evaluation and Recovery (weight 6%, 0%) — spec Phases 14, 15

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| EV-01 | P0 | Evaluation engine: deterministic checks (tests, lint, diff, acceptance criteria) + optional LLM review | Evidence-based result | pytest | RT-02 | pending | — |
| EV-02 | P0 | Performance profiles by agent/role/provider/model/task type; feed assignment | Profiles update; used by OR-02 | pytest | EV-01 | pending | — |
| EV-03 | P0 | Failure categorization + recovery workflow + circuit breaker | Recovery tests | pytest | RT-06, RT-08 | pending | — |
| EV-04 | P0 | Graceful shutdown preserving partial results | Shutdown test | pytest | RT-06 | pending | — |
| EV-05 | P0 | Evaluation UI | E2E | Playwright | EV-01 | pending | — |

## M13 — Enterprise Security (weight 7%, 38%) — spec Phases 16, 17, 22

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| SEC-01 | P0 | RBAC: 6 roles + permissions, server-side enforcement on every endpoint | Authorization matrix tests | pytest | FND-04 | done | 6 roles/38 permissions seeded; require() on every route; tests test_rbac.py |
| SEC-02 | P0 | Agents cannot escalate own permissions or change security policy | Tests | pytest | SEC-01 | done | agents have no login/roles; tool ceilings per security profile on create/edit; no merge/deploy tools exist; role grants limited to caller's own permissions (tests) |
| SEC-03 | P0 | Approval Center: risk levels, evidence, decision records; critical ops blocked until approved | Tests + E2E | pytest + Playwright | SEC-01 | pending | — |
| SEC-04 | P0 | Docker sandbox: CPU/mem/time/pids limits, no network by default, workspace-only mount, no secrets | Real container tests | pytest (docker) | — | pending | — |
| SEC-05 | P0 | Command policy (default deny, allowlist per tool permission) | Tests | pytest | SEC-04 | pending | — |
| SEC-06 | P0 | Audit log searchable/filterable, redacted; Audit UI | Tests + E2E | pytest + Playwright | FND-06 | pending | — |
| SEC-07 | P1 | CSRF protection for cookie auth, secure CORS, security headers | Tests | pytest | FND-04 | done | CSRF double-submit, no CORS, security headers; tests pass |
| SEC-08 | P1 | Dependency + container scanning in CI scripts | Scan runs | script | — | pending | — |

## M14 — Enterprise Experience (weight 5%, 0%) — spec Phases 21, 23, 24

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| UX-01 | P0 | 20-item navigation, every page functional (no dead buttons) | E2E visits each page | Playwright | — | pending | — |
| UX-02 | P0 | Real-time updates via WebSocket across dashboard/tasks/executions | E2E sees live change | Playwright | RT-04 | pending | — |
| UX-03 | P0 | Execution timeline view | E2E | Playwright | RT-02 | pending | — |
| UX-04 | P0 | Analytics: cost/usage by provider/model/agent/project | API + UI tests | pytest + Playwright | GW-05 | pending | — |
| UX-05 | P1 | Observability: structured logs, OpenTelemetry traces, health of all services | Health endpoint covers all; traces exported | pytest | — | pending | — |
| UX-06 | P1 | Automation & scheduling (pause/resume/edit/delete/history), in-app only | Tests | pytest | OR-01 | pending | — |

## M15 — Final Integration (weight 3%, 0%) — spec Phases 25, 26, F

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| FI-01 | P0 | Windows scripts: START/STOP/UPDATE/RESET-DEV.bat, INSTALL.ps1, HEALTH-CHECK.ps1 | Static checks here; real Windows run | script + Windows | — | pending | — |
| FI-02 | P0 | Linux/macOS scripts: start/stop/update/install/health-check.sh | Run in dev env | shell | — | pending | — |
| FI-03 | P0 | Production compose config | docker compose config | compose | FND-13 | pending | — |
| FI-04 | P0 | 21 documentation files reflecting actual implementation | Exist + reviewed | inspection | — | pending | — |
| FI-05 | P0 | End-to-end Definition-of-Done scenario with Mock provider | E2E passes | Playwright + pytest | — | pending | — |
| FI-06 | P0 | End-to-end with real provider + GitHub | Live run | manual/live | GW-10 | pending | — |
| FI-07 | P0 | Release readiness report + known limitations | Docs | inspection | — | pending | — |
