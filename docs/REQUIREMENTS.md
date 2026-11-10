# Requirements Matrix

Generated from `docs/requirements.json` by `scripts/progress.py`. Spec: `docs/MASTER_PROMPT.md` v4.0.

**Overall verified completion: 83.2%**

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

## M4 — Multi-Provider AI Gateway (weight 8%, 70%) — spec Phases 9, 20

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| GW-01 | P0 | Provider adapter interface + registry (Anthropic, OpenAI, Google, Mock) | Adapters implement contract; Mock clearly labeled | pytest | RT-01 | done | ProviderAdapter + registry: anthropic, openai, openai_compatible, google, mock (labelled); tests |
| GW-02 | P0 | Provider + model registry with metadata/capabilities and pricing | CRUD + model list | pytest | GW-01 | done | providers + models registry with capabilities/tier/prices; add/patch/discover; tests |
| GW-03 | P0 | Encrypted API credentials, never returned in full, never in prompts | Masking tests | pytest | FND-06 | done | Fernet-encrypted credentials, write-only API (hint …WXYZ), rotation, never in audit/prompts; test |
| GW-04 | P0 | Connection test per provider | Mock passes; real adapters tested with HTTP mocks | pytest | GW-01 | done | POST /providers/{id}/test updates status; tests via HTTP mocks |
| GW-05 | P0 | Token + cost accounting per execution (usage_records/cost_records); no fabricated prices | Recorded per call; unknown price = null | pytest | GW-01 | done | usage_records per call; cost_records only with owner prices (exact cached/uncached math); unknown price => null; tests |
| GW-06 | P0 | Fallback provider/model on failure | Primary failure → fallback used + logged | pytest | GW-01 | done | primary→fallback on provider failure, audited gateway.fallback; no splicing after partial output; test |
| GW-07 | P1 | Routing by complexity/capability/cost/availability | Routing tests | pytest | GW-02 | pending | — |
| GW-08 | P1 | Budgets: agent/project/task/daily/monthly; stop new work at limit + approval request | Budget tests | pytest | GW-05 | pending | — |
| GW-09 | P0 | Providers & Models UI | E2E add provider, test connection | Playwright | GW-02 | done | Providers & models page: add provider (write-only key), set/rotate key, test, discover, enable/disable, delete, add/edit model prices; E2E + axe ar/en |
| GW-10 | P0 | At least one real official API call verified | Live call with owner key succeeds | manual/live test | GW-01 | pending | — |

## M5 — Execution Engines (weight 6%, 50%) — spec Phase 10

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| EE-01 | P0 | API execution engine (via gateway) | Runs task with Mock end-to-end | pytest | RT-01, GW-01 | done | real 'api' engine runs executions through the gateway end to end; tests |
| EE-02 | P1 | CLI execution engine adapter (sandboxed subprocess) with documented support for Claude Code / Codex / Gemini CLI / OpenCode | Adapter + docs; verified flags only | pytest + docs | RT-01, SEC-04 | pending | — |
| EE-03 | P1 | External agent runtime adapter (A2A client) | Adapter contract test | pytest | A2A-02 | done | tests/test_a2a.py::test_a2a_engine_delegates_to_external_agent_and_reattaches_on_resume (platform agent → A2A provider → remote agent over HTTP; resume re-attaches without resubmitting), test_a2a_engine_unreachable_service_fails_cleanly |
| EE-04 | P0 | Unsupported integrations explicitly documented | PROVIDER_INTEGRATION.md table | inspection | — | pending | — |

## M6 — Intelligent Orchestration (weight 9%, 89%) — spec Phases 4, 13

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| OR-01 | P0 | Objective → plan → task DAG (LangGraph) | Plan creates tasks + dependencies (mock provider) | pytest | RT-01, AG-01 | done | tests/test_orchestration.py::test_langgraph_planner_creates_task_dag_and_resumes_from_checkpoint (LangGraph + PostgresSaver, mock provider → 4 tasks + 4 dependency edges), test_plan_validation_rejects_bad_graphs, test_invalid_plans_are_retried_with_feedback_then_fail_the_workflow |
| OR-02 | P0 | Agent selection by skills, permissions, workload, availability, cost, history | Selection tests | pytest | OR-01 | done | tests/test_orchestration.py::test_agent_selection_filters_then_scores (roster, required skills, history, workload, availability; audited score breakdown) |
| OR-03 | P0 | Parallel fan-out/fan-in of independent tasks; dependency-driven scheduling | Independent tasks run concurrently | pytest | MQ-02 | done | test_fan_out_fan_in_with_review_gate (rounds 1/2/1; fan-in prompt carries prerequisite outputs), test_failure_retries_then_blocks_dependents; test_bus::test_workflow_runs_end_to_end_over_bus_and_survives_worker_kill (both branches dispatched in one round, run by workers) |
| OR-04 | P0 | Review loop and human approval stage | Review requests revision; approval stage blocks | pytest | OR-01, SEC-03 | done | test_review_revise_loop_feeds_note_into_next_attempt, test_critical_task_waits_for_approval_while_independent_work_continues, test_rejected_approval_fails_the_task_and_blocks_dependents; e2e approvals.spec.ts critical task flow |
| OR-05 | P0 | Workflow persistence and resume after restart | Restart mid-workflow resumes | pytest | OR-01 | done | test_bus::test_workflow_runs_end_to_end_over_bus_and_survives_worker_kill (kill -9 mid-workflow, reviews recorded while down, new worker completes, every task attempts==1); planner resumes from LangGraph checkpoint without re-calling the model |
| OR-06 | P0 | Duplicate execution prevention | Same idempotency key never runs twice | pytest | MQ-03 | done | test_duplicate_events_and_advances_never_run_work_twice (duplicate advance + duplicate execution.finished), task execution idempotency key task:{id}:attempt:{n}; API workflow idempotency_key → same workflow |
| OR-07 | P0 | Task center: Kanban (9 columns) + list + details with acceptance criteria, evidence, history | E2E | Playwright | OR-01 | done | frontend/e2e/tasks.spec.ts: objective → mock-planned task → worker execution → REVIEW column → details (acceptance criteria, evidence, history, deep link) → note required for changes → approve → COMPLETED + workflow completed; list view; axe WCAG 2.2 AA + no page overflow at 375/1440 in ar/en. Kanban has the spec's 9 columns; screenshots reviewed in RTL and LTR |
| OR-08 | P0 | Tasks never marked complete on agent claim alone (evidence gate) | Completion requires passing evaluation | pytest | EV-01 | done | test_human_cannot_approve_without_a_passing_evaluation (409 evaluation_not_passed); evaluator revise/reject never reaches REVIEW (test_reviewer_model_can_withhold_but_never_grant_approval) |
| OR-09 | P1 | Project modes: new / existing (repo inspection, tech detection) / maintenance | Existing-repo analysis produces map | pytest | GH-02 | pending | — |

## M7 — Agent Supervisor (weight 6%, 100%) — spec Phase 5

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| SV-01 | P0 | Independent supervisor service monitoring agent/execution states | Separate module/worker | pytest | RT-03 | done | app/supervisor as a separate worker role with leader lock; tests/test_supervisor.py::test_supervisor_api (health/findings/actions/run, permissions) |
| SV-02 | P0 | Detect stalled, repeatedly failing, runaway-cost, excessive-retry executions | Detection tests | pytest | SV-01 | done | test_stalled_execution_is_resumed_through_the_bus, test_runaway_execution_is_stopped, test_repeated_failures_isolate_the_agent_until_released, test_policy_denials_are_suspicious_and_reduce_concurrency (excessive retries/idle detectors in the same sweep) |
| SV-03 | P0 | Policy-authorized reversible actions: retry, reassign, stop runaway, isolate agent, fallback model | Action tests + audit | pytest | SV-02 | done | actions tested: resume via bus (once, deduplicated), stop runaway, isolate + release, reduce concurrency, retry transient task within budget; test_actions_can_be_made_advisory_only; all audited |
| SV-04 | P1 | Detect deadlocks, duplicate work, conflicting changes | Detection tests | pytest | SV-01 | done | test_deadlock_duplicates_conflicts_and_idle_agents_are_detected (advisory, self-resolving findings) |
| SV-05 | P0 | Supervisor never overrides security/approval policies | Test: approval-gated op not executed by supervisor | pytest | SEC-03 | done | test_supervisor_never_overrides_approvals_or_touches_tasks_awaiting_humans (approval stays pending, task in APPROVAL untouched, no code path to approvals) |
| SV-06 | P0 | Supervisor UI: health, findings, actions log | E2E | Playwright | SV-01 | done | frontend/e2e/supervisor.spec.ts: health tiles, manual sweep, findings/all-clear, log; axe WCAG 2.2 AA ar/en (caught and fixed an invalid definition list), no overflow 375/1440 |

## M8 — GitHub Development (weight 7%, 100%) — spec Phase 12

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| GH-01 | P0 | GitHub auth (fine-grained token or GitHub App) stored encrypted | Credential tests | pytest | GW-03 | done | tests/test_github.py::test_token_is_verified_stored_encrypted_and_never_returned (bad token 400, ciphertext only, hint only in API, human-only credentials.manage, duplicate 409) |
| GH-02 | P0 | Repos, branches, commits, PRs, diffs via API | Tests with HTTP mocks | pytest | GH-01 | done | tests/test_github.py::test_client_reads_and_maps_errors against an HTTP fake (branches, commits, compare, check runs, 401 non-retryable, rate limit/5xx/transport retryable, https only) |
| GH-03 | P0 | Per-agent git worktree + feature branch; track files/ownership/conflicts | Real git worktree tests | pytest | — | done | tests/test_devplane.py on real git 2.43: per-task worktrees on agent branches, isolation between tasks, modified files + overlap tracking, author/committer, hooks never execute (even when the repo re-enables them), path/symlink escapes refused, sandbox pytest runs inside the task worktree |
| GH-04 | P0 | Protected branches cannot be modified by agents | Push to main rejected by policy | pytest | GH-03, SEC-01 | done | test_protected_branches_cannot_be_written_by_agents: protected branch target refused + audited; detached worktree commit refused; base repo stays on main and untouched by agent work |
| GH-05 | P0 | PR creation with description + test results | PR payload test | pytest | GH-02 | done | test_pr_is_pushed_described_and_merged_only_after_approval: branch pushed to a real bare remote (main untouched), PR body contains criteria, changed files, mock warning and the approval notice |
| GH-06 | P0 | Merge only via approved approval request | Unapproved merge blocked | pytest | SEC-03 | done | test_merge_happens_only_through_an_approved_request (nothing merged while pending; direct execute refused; approve → --no-ff merge of the frozen commit → task COMPLETED), test_branch_moved_after_approval_request_is_not_merged, test_textual_conflict_with_base_blocks_review_approval |
| GH-07 | P1 | Webhooks + CI check status | Webhook signature verified | pytest | GH-02 | done | test_webhooks_are_signed_deduplicated_and_update_checks: bad/missing signature and unknown repo 401, replayed delivery ignored, check_run updates PR checks, pull_request merged updates state |
| GH-08 | P0 | GitHub UI page | E2E | Playwright | GH-02 | done | frontend/e2e/github.spec.ts: connect dialog validation + honest failure when GitHub unreachable (nothing stored), repositories with protected branches, PR/worktree sections, axe WCAG 2.2 AA ar/en with dialog open, no overflow 375/1440 |

## M9 — Browser Bridge and MCP (weight 7%, 100%) — spec Phases 11, 18

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| BB-01 | P0 | Reference evaluation of ai-browser-bridge (license, platform, security, ToS) | Report in docs | inspection | — | done | docs/BROWSER_BRIDGE.md §1: license MIT, macOS-only, Node/CDP, shared profile, public tunnel, ToS; decision ADR-025 |
| BB-02 | P0 | Browser Bridge service: isolated profiles, 9 session states, Playwright, DISABLED by default + EXPERIMENTAL label | State machine tests against local test page | pytest/Playwright | BB-01 | done | backend/tests/test_browser.py (20 tests, real headless Chromium vs local test page): 9-state table, start/prompt/stream/cancel/stop/restart, login/captcha/interface_changed, session limit, orphans; UI e2e/browser-bridge.spec.ts ar+en; disabled by default + EXPERIMENTAL labels |
| BB-03 | P0 | Per-provider ToS gate: provider adapter cannot be enabled until owner acknowledges terms | Gate test | pytest | BB-02 | done | test_disabled_by_default_and_per_provider_terms_gate + test_gate_rechecked_on_every_prompt: exact statement, Super-Admin-only, audit, revoke relocks |
| BB-04 | P0 | Never expose cookies/secrets; no CAPTCHA bypass; dedicated profiles only | Security tests | pytest | BB-02 | done | test_only_dedicated_profile_directories (7 cases), test_bridge_code_has_no_path_to_cookies_or_storage, page cookie never leaves (TOPSECRET check), captcha untouched |
| MCP-01 | P0 | MCP client: register servers, discover tools/resources, health, permission enforcement | Test against local MCP server | pytest | RT-07 | done | tests/test_integrations.py against local stdio MCP server fixture: register+assessment, discover tools/resources, health, unreachable→502, agent calls enforced (disabled/unassigned/tool disabled → allowed), audit |
| MCP-02 | P0 | MCP server exposing platform tools (incl. Browser Bridge tools) | MCP client can list/call tools | pytest | MCP-01 | done | MCP SDK client lists/calls platform tools over streamable HTTP with key scoping, 401 missing/revoked, registry client discovers platform server; browser tools gated |
| MCP-03 | P0 | Skills & MCP registry UI with security assessment before enable | E2E | Playwright | MCP-01 | done | frontend/e2e/integrations.spec.ts ar+en: key once, register+discover+risk dialog+enable, tool enable, skill inspect→install disabled, axe, no overflow; full E2E 55/55 |
| MCP-04 | P1 | Skill install with source inspection (dangerous commands, network, fs, credentials) | Scanner tests | pytest | — | done | test_scanner_flags_every_category, test_mcp_server_assessment, test_skill_install_inspect_enable_assign_and_context (high risk needs accept_risks; context only when enabled+assigned) |

## M10 — A2A and Communication (weight 5%, 100%) — spec Phase 6

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| MQ-01 | P0 | RabbitMQ topology: exchanges by event type, queues by specialty/priority, DLQ | Topology declared; test | pytest (live broker) | — | done | tests/test_bus.py::test_topology_declared_with_dlq_priorities_and_specialty_routing — live RabbitMQ 3.12: exchanges, specialty routing, mandatory unroutable refusal, priority order, DLQ |
| MQ-02 | P0 | Structured message contract (ids, correlation, schema version) + ack + retries + timeouts | Contract + consumer tests | pytest (live broker) | MQ-01 | done | test_contract_fields_and_validation, test_outbox_is_transactional_and_relay_publishes_with_confirms, test_retry_with_delay_then_success, test_timeout_poison_fatal_and_exhausted_go_to_dlq — live broker |
| MQ-03 | P0 | Idempotent consumers (dedupe by idempotency key) | Duplicate delivery processed once | pytest | MQ-02 | done | test_duplicate_delivery_processed_once (same message twice + producer re-send → handler once, ledger 'done'), test_busy_work_key_is_deferred_not_run_twice |
| MQ-04 | P0 | Recovery after broker/worker restart | Restart test | pytest/compose | MQ-02 | done | test_worker_killed_mid_execution_is_recovered_by_another (kill -9 mid-run → new worker resumes from checkpoint, output exact), test_broker_restart_loses_nothing (rabbitmqctl stop_app/start_app: outbox waits, worker reconnects; persistent queued message survives) |
| A2A-01 | P1 | A2A agent card + task endpoint for platform agents | Spec-compatible JSON | pytest | — | done | tests/test_a2a.py (21 tests): agent card fields, key-gated exposure (401/404), human-only hashed audited keys, revocation, SendMessage blocking/returnImmediately, idempotent messageId, GetTask, ListTasks filter+pagination, CancelTask, per-key task privacy, 11 spec error codes; test_bus::test_a2a_task_runs_through_bus_and_worker; live smoke on dev stack (API→outbox→RabbitMQ→worker) |
| A2A-02 | P1 | A2A client for external agent services | Client test against local server | pytest | A2A-01 | done | tests/test_a2a.py::test_client_against_local_server (real uvicorn over TCP: card discovery, send blocking/non-blocking, wait, list, error mapping, bad token), test_client_refuses_card_that_redirects_credentials_elsewhere |

## M11 — Context and Memory (weight 7%, 100%) — spec Phases 7, 8

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| CX-01 | P0 | Context assembler with per-task-type profiles and token budget | Assembles within budget; tests | pytest | RT-01 | done | test_assembles_within_budget_per_profile (4 profiles/budgets) + test_context_preview_endpoint; scheduler uses assembler |
| CX-02 | P0 | Trust separation: system/policy/requirements/user/retrieved/tool-output segments; retrieved content cannot override policy | Injection tests | pytest | CX-01 | done | 4 parametrized injection tests (EN/AR/tag forgery) + nonce forgery + project memory injection stays data; system message is policy-only |
| CX-03 | P0 | Context dedup, prioritization, compression/summarization | Tests | pytest | CX-01 | done | test_dedup_prioritization_and_compression |
| MEM-01 | P0 | Scoped memory store (9 types) with provenance, verified vs assumption flag, versioning, expiry | CRUD + scope isolation tests | pytest | FND-03 | done | backend/tests/test_memory.py: scope rules, provenance, agents cannot verify, versioning/history, expiry, delete chain, viewer read-only (197/197 backend) |
| MEM-02 | P0 | Semantic retrieval | Relevant memories ranked first | pytest | MEM-01 | done | test_relevant_memories_rank_first (EN + AR + word-form); hybrid ts_rank + local hashed embedding |
| MEM-03 | P0 | Project isolation of memory | Cross-project retrieval denied | pytest | MEM-01 | done | test_cross_project_and_cross_tenant_retrieval_denied (store, API, other tenant 404, task prompt) |
| MEM-04 | P0 | Context & Memory UI with human correction/deletion | E2E | Playwright | MEM-01 | done | frontend/e2e/memory.spec.ts ar+en: add, ranked search, verify, correct with history, delete, context preview, axe WCAG 2.2 AA, no overflow 375/1440; full E2E 51/51 |

## M12 — Evaluation and Recovery (weight 6%, 100%) — spec Phases 14, 15

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| EV-01 | P0 | Evaluation engine: deterministic checks (tests, lint, diff, acceptance criteria) + optional LLM review | Evidence-based result | pytest | RT-02 | done | test_evaluator_is_deterministic_and_reports_each_criterion, test_reviewer_model_can_withhold_but_never_grant_approval; every task execution in test_orchestration/test_bus/e2e passes through evaluate_and_record (idempotent per execution) |
| EV-02 | P0 | Performance profiles by agent/role/provider/model/task type; feed assignment | Profiles update; used by OR-02 | pytest | EV-01 | done | tests/test_orchestration.py::test_profiles_update_from_evaluations_and_steer_assignment (profiles by agent/role/provider/model/task_type/project from evaluation_results; neutral prior below 3 samples; selection prefers the agent with the better evaluated record for that task type; audited quality factor) |
| EV-03 | P0 | Failure categorization + recovery workflow + circuit breaker | Recovery tests | pytest | RT-06, RT-08 | done | tests/test_recovery.py (5): categories, recovery report with evidence/budget/escalation + audit and partial output kept, agent circuit open → 409 + unassignable → half-open trial → closed (all audited), policy denials don't open circuits, provider circuit skips the failing provider and uses only the fallback |
| EV-04 | P0 | Graceful shutdown preserving partial results | Shutdown test | pytest | RT-06 | done | tests/test_bus.py::test_sigterm_lets_short_work_finish_and_hands_off_long_work: real worker SIGTERM with 3 s grace → exits 0, short execution COMPLETED inside grace, long one TERMINATED with partial output 'one two ' and a shutdown event, new worker resumes from checkpoint → exactly 'one two three' |
| EV-05 | P0 | Evaluation UI | E2E | Playwright | EV-01 | done | frontend/e2e/evaluations.spec.ts: /evaluations performance profiles grouped (URL state), recent verdicts linked to tasks, no page overflow 375/1440, axe WCAG 2.2 AA ar/en; per-task evaluation (checks, criteria coverage, notes) in task details covered by tasks/approvals specs |

## M13 — Enterprise Security (weight 7%, 88%) — spec Phases 16, 17, 22

| ID | P | Requirement | Acceptance | Verification | Depends | Status | Evidence |
|---|---|---|---|---|---|---|---|
| SEC-01 | P0 | RBAC: 6 roles + permissions, server-side enforcement on every endpoint | Authorization matrix tests | pytest | FND-04 | done | 6 roles/38 permissions seeded; require() on every route; tests test_rbac.py |
| SEC-02 | P0 | Agents cannot escalate own permissions or change security policy | Tests | pytest | SEC-01 | done | agents have no login/roles; tool ceilings per security profile on create/edit; no merge/deploy tools exist; role grants limited to caller's own permissions (tests) |
| SEC-03 | P0 | Approval Center: risk levels, evidence, decision records; critical ops blocked until approved | Tests + E2E | pytest + Playwright | SEC-01 | done | tests/test_approvals.py (6): frozen payload hash, risk never lowered, idempotent requests, executed exactly once with config version, tampered payload refused but decision kept, profile ceiling not liftable, HUMAN_ONLY decide + no self-approval + reason required, pending-first listing, 7-day expiry, tenant isolation; frontend/e2e/approvals.spec.ts: critical task → approval center (ack required for critical, reason required for reject) → approve → task COMPLETED; axe WCAG 2.2 AA ar/en incl. open dialog |
| SEC-04 | P0 | Docker sandbox: CPU/mem/time/pids limits, no network by default, workspace-only mount, no secrets | Real container tests | pytest (docker) | — | done | tests/test_sandbox.py against real Docker 29 containers (image acc/sandbox:python, debootstrapped Ubuntu 24.04 + pytest): uid 10001 with CapEff 0, no network, read-only rootfs + workspace-only write + no platform env/secrets/host paths, memory limit kills, pids limit, time limit kills and leaves no container, NanoCpus/PidsLimit/ReadonlyRootfs/NetworkMode verified via docker inspect, workspace boundary incl. symlink escape; agent → shell.test → sandbox pytest run, audited |
| SEC-05 | P0 | Command policy (default deny, allowlist per tool permission) | Tests | pytest | SEC-04 | done | tests/test_sandbox.py::test_command_policy_default_deny (17 cases: per-tool allow-lists, no shells, no traversal/absolute paths, no code-loading flags, argv only), runtime test: denied command returned to the agent and audited as sandbox.command_denied |
| SEC-06 | P0 | Audit log searchable/filterable, redacted; Audit UI | Tests + E2E | pytest + Playwright | FND-06 | done | tests/test_audit.py (filters, severity threshold, literal search, keyset paging, tenant isolation, redaction on output, CSV export formula-safe, read-only API); frontend/e2e/audit.spec.ts (search, details dialog, severity filter in URL and export link, CSV download) + axe WCAG 2.2 AA ar/en with dialog open |
| SEC-07 | P1 | CSRF protection for cookie auth, secure CORS, security headers | Tests | pytest | FND-04 | done | CSRF double-submit, no CORS, security headers; tests pass |
| SEC-08 | P1 | Dependency + container scanning in CI scripts | Scan runs | script | — | in_progress | — |

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
