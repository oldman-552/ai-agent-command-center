# Changelog

All notable changes. The project follows the milestones of the master specification (v4.0); requirement IDs refer
to `docs/requirements.json`.

## Unreleased — 2026-11-11 — AI Workforce

### Removed
- **Arena site of the Browser Bridge (ADR-032).** The built-in `arena` adapter, its guessed selectors and its terms
  text are gone. Arena addresses (`arena.ai`, `lmarena.ai` and subdomains) are refused as custom sites, at the gate,
  in the bridge service and through the MCP browser tools (`arena_requires_local_bridge`). Migration `b1a7e0a3c032`
  archives stored Arena acknowledgements, selector overrides and custom Arena sites under
  `browser_bridge.retired_arena` and stops Arena browser sessions; nothing is deleted and it is reversible.
  The generic Browser Bridge (sites you add, profiles, desktop mode, `browser` engine, MCP tools) is unchanged.

### Added
- `BROWSER_EXECUTABLE_PATH`: the Browser Bridge can use an installed Chrome/Chromium.
- `docs/WORKFORCE_ASSESSMENT.md` and ADR-032: assessment of this repository and of Arena Local Bridge.
- **Arena Local Bridge provider** (`arena_local_bridge`, ADR-032, `docs/ARENA_LOCAL_BRIDGE.md`): the only Arena path.
  Own HTTP adapter (stream and non-stream, heartbeats, connect/idle/total timeouts, `Host` header for
  `host.docker.internal`, stable session and idempotency headers, every bridge error classified, request id kept).
  Usage is recorded as *not reported* — never as zero tokens. A request with an unknown outcome is resent only
  inside the bridge's replay window, otherwise the execution fails with `outcome_unknown`.
- **Bridge settings** at `/api/workforce/bridges`: several instances, encrypted write-only key, exact-statement terms
  gate per instance (revoked when the address changes), step-by-step diagnostics that name the failing step,
  session discovery, masked account list, account-binding classification (pinned session / single account / pooled /
  unknown — never "attested"). New permissions `workforce.read|run|work|review|manage|bridge`; `workforce.bridge`
  is Super Admin only.
- Worker role `workforce`: reads each enabled bridge's health every `ARENA_BRIDGE_HEALTH_INTERVAL_SECONDS` (one
  leader). `/api/health` gains an `arena_local_bridge` entry (counts only).
- `backend/devtools/fake_arena_bridge.py`: a FAKE bridge for tests and demos, built from the bridge's source. It
  identifies itself, and everything that passes through it is labelled mock.
- `scripts/dev/no-docker/`: build Python, PostgreSQL, Redis, RabbitMQ and Chromium as plain user processes to run the
  test suites on a machine without Docker or root (developer convenience; not used by the installers).
- **AI Workforce backend** (ADR-033, `/api/workforce`): one prompt → the Manager analyses and plans → four workers →
  review → one report.
  - A default workforce is created at start: a Manager **without a model** and four workers (research, engineering,
    QA, documentation), all **Human-Assisted**. Nothing is sent anywhere until a person chooses a Manager model.
  - Worker modes: Automatic (through a provider such as an Arena Local Bridge session), Human-Assisted (a person takes
    the card, gets the exact brief, and submits text, notes and attachments), Disabled. An Automatic worker that
    cannot run now hands its task to a person (optional per worker).
  - Task lifecycle with a single table of allowed moves and a history row for every change.
  - Failed Automatic attempts are classified and bounded: retried, handed to a person, or failed visibly — and a
    request whose outcome is unknown is never resent automatically.
  - Review: the Manager's verdict is advice; fixed platform rules decide (an unmet criterion is never an approval,
    a revision limit, instruction-like or credential-like text goes to a person). Critical tasks are completed only
    by a human approval of exactly the reviewed version.
  - Consolidated report (`/objectives/{id}/report`, `report.md`): every result in one of six classes computed from
    recorded evidence — verified, approved by a person, worker proposal, unverified claim, failed, incomplete. The
    Manager's summary is labelled as model text. Mock results are labelled.
  - History (`/objectives/{id}/history`): Manager steps, tasks, workers, attempts, provider request ids, retries and
    errors; usage only where a provider reported it.
  - Attachments of Human-Assisted results are stored in the new `workforce-attachments` volume.
- **AI Workforce pages** (WF-16, Arabic and English, second item of the navigation):
  - *Overview* (`/workforce`): the prompt form, what is waiting for you, the Manager and the four workers with their
    mode and account binding, bridge status, latest objectives, recent activity. Without a Manager model the form is
    disabled and says why.
  - *Tasks* (`/workforce/tasks`): a board with six columns, queues (offered to me, accepted by me, needs a person's
    review, open) and filters by objective, worker, mode, state and text. Filters live in the address.
  - *Task card*: the exact brief with a copy button, accept / decline, submit text, notes and files, every version
    with its review, the person's review box, reassign / reopen / cancel, failures and the state history.
  - *Manager & workers* (`/workforce/workers`): the Manager's model, fallback and review policy; each worker's mode,
    provider, session, person, timeout and attempts.
  - *Arena Local Bridge* (`/workforce/bridge`): add, test with step-by-step diagnostics, terms acknowledgement,
    session discovery, masked accounts, replace the key, disable, delete. The key is never shown again.
  - *Objective* (`/workforce/objectives/{id}`): the plan, the consolidated result (the six classes, the platform's
    own statements in the reader's language, the Manager's summary labelled as model text, Markdown download) and the
    history (identifiers, Manager steps, automatic attempts, versions, state changes).
  - Everything mock is labelled: the mock Manager, the fake bridge, and each result that passed through them.
- **Approval Center**: an approval raised by the AI Workforce links to the workforce task card and says which version
  of the result it accepts.
- `scripts/dev/no-docker/e2e-stack.sh`: the whole platform plus the fake bridge as plain processes, for the browser
  suite on a machine without Docker.
- **Bridge probe** `python -m app.connectivity.arena_bridge.probe`: the diagnostics of the bridge page from the
  command line, for every configured bridge, or for the default address when none is configured. Sends nothing to
  Arena; prints no key and no account address.
- **Launch scripts** (WF-17; Windows files checked as text only, never executed):
  - `HEALTH-CHECK.bat` / `health-check.sh` run the probe **inside the API container and the worker container** and
    show the `arena_local_bridge` line of the health report. An unusable bridge is a warning, not a failure.
  - `INSTALL.bat` (same as `INSTALL.ps1`) and `RESET.bat` (same as `RESET-DEV.bat`; the warning now lists what is
    deleted, including bridge settings and stored keys, and says that Arena Local Bridge itself is not touched).
  - `REAL-TESTS.bat`: the two checks that leave the machine, each after a typed confirmation — the Manager on a real
    model (WF-21) and four workers on a real Arena Local Bridge (WF-20). Keys are typed hidden and never written.
  - Compose service `realtests` (own profile; never started by START): runs those checks in a throwaway container
    on a separate database, without access to the installation's broker or Redis.
- **Test tiers `real_provider` and `real_arena`** (`tests/test_real_provider.py`, `tests/test_real_arena.py`),
  skipped unless the owner supplies the settings. `tests/test_real_tiers_selftest.py` runs the same steps on the
  mock provider and the fake bridge.
- `docs/AI_WORKFORCE.md`: the user guide, including Human-Assisted work step by step.

### Fixed
- **Two people on the critical path** (ADR-033 addendum). A person who did a Human-Assisted task could submit a
  result for a critical task and then approve it alone in the Approval Center, because the approval was requested
  by `workforce`. The requester is now the person who submitted the result, so the Approval Center refuses their
  own decision (`self_approval_forbidden`); the task card says that someone else has to approve. An Automatic
  worker's result is unchanged.
- **The test suite could erase a real database.** It drops and rebuilds the schema of whatever
  `TEST_DATABASE_URL` names. It now refuses any database whose name does not end in `_test`.
- **Windows launcher and program output.** `docker` and `git` write normal progress to the error stream. Under
  Windows PowerShell 5.1 with `$ErrorActionPreference = "Stop"`, a redirected error stream can end the script at the
  first such line. Every program call now goes through a wrapper that relaxes the preference for that call and
  decides by the exit code. Not observed in a run — the scripts were never executed — but it is a known behaviour.
- **Page title during navigation.** The title came from asynchronous metadata, which is streamed: after every change
  of the address the document had no `<title>` for a moment (measured: 85 empty readings over 24 navigations, 0 after
  the fix). The accessibility check `document-title` failed intermittently because of it. The title is now rendered
  by the root layout.
- **Agent state chip contrast.** Green and amber text on a tinted chip measured 4.38:1 (4.5:1 is required for small
  text). The chip is now neutral with full-contrast text; the colour tints only the icon, as on the task board.

### Changed
- Executions and usage records can carry *unknown* usage: token counters and cost are empty instead of 0 when the
  provider reports nothing. API consumers must treat `null` as "not reported".
- A transient provider failure honours the provider's `Retry-After` in the retry back-off.
- The Manager and the workers of the AI Workforce are configured only at `/api/workforce`; the generic agent routes
  answer 409 `managed_at:…` for them, and the standard orchestrator never assigns work to them.
- A prompt given to the AI Workforce is redacted like every other input before it is stored or sent; the response
  says so (`prompt_redacted`).

### Not verified
- The Manager on a real Claude model and Automatic workers on a real Arena Local Bridge. Every test used the built-in
  mock provider as the Manager and the fake bridge; both are labelled as such in results and reports.
- **The Windows scripts** (`*.bat`, `scripts/windows/acc.ps1`), including `HEALTH-CHECK.bat` and `REAL-TESTS.bat`:
  never executed. No Windows and no Docker were available. Checked as text only
  (`backend/tests/test_launch_scripts.py`); the Linux health check ran against stand-ins for `docker` and `curl`;
  the command the `realtests` container runs was executed outside Docker in a folder laid out like the image.
- Colour contrast of **Arabic** text is not checked by the automated accessibility tests: axe-core skips text it
  takes for an icon ligature, and joined Arabic letters match that heuristic (see `docs/DEVELOPMENT.md`). The same
  components with the same colours are checked in the English run.
- Development tasks from text workers (files applied to a worktree, WF-14) are not implemented, so the report class
  "verified" stays empty for now.

## Unreleased — 2026-11-10

### Added
- **Browser Bridge rework** (owner decision): only arena.ai built in (Claude/ChatGPT/Gemini adapters removed), **Add
  site** for any website, **one browser profile per employee, agent or account** shared across sites (one window at a
  time), and **desktop mode** (`./bridge-desktop.sh`) with visible windows on your own computer.
- **Browser Bridge: arena.ai adapter** (experimental, owner request; terms clauses acknowledged explicitly) and
  **selector overrides** per provider for the Super Admin, so a changed page can be fixed without a code change.
- The Browser Bridge service is optional in the default compose file (`COMPOSE_PROFILES=browser-bridge`), so a first
  install no longer downloads the ~1.5 GB Playwright image.
- **Agents can ask a person** (`ask.human`): the execution waits in `WAITING_FOR_INPUT`, the question appears in the
  execution timeline with an answer box, A2A clients answer with `taskId`; the run resumes from its checkpoint.
- **Model tool calling** in the `api` engine for Anthropic, OpenAI/compatible and Gemini: permitted platform and MCP
  tools are offered with schemas, run through the runtime's permission check, and fed back in a bounded, checkpointed
  loop. Previously only mock scripts and CLIs could use tools.
- **Reverse proxy for several API replicas**: `deploy/nginx/acc.conf` (TLS, WebSocket, streaming, `least_conn`),
  tested with a real nginx in front of two API processes.
- **Memory search mode**: optional embedding model (OpenAI / OpenAI-compatible, e.g. local Ollama) next to the local
  index; background indexing, fallback and breaker; settings card on the Memory page with a data-egress notice.
- **A2A extended card and multi-turn**: `GetExtendedAgentCard`; conversations continue across tasks in a `contextId`,
  `referenceTaskIds` and `taskId` follow-ups (per key, bounded, fenced).
- **A2A push notifications**: the four config methods and `taskPushNotificationConfig` in SendMessage; webhook
  deliveries via a transactional outbox, retries with backoff, SSRF guard (spec §13.2).
- **Agent form: command-line tool settings** — tool, key source (the agent's provider), provider-kind check, OpenCode key
  variable and the project-configuration opt-in; previously a CLI agent could not be configured from the UI.
- **CLI engine hardening**: Gemini headless runs need `--skip-trust` (found by running the CLIs in the sandbox); the engine
  refuses workspaces with CLI project configuration (`.gemini/`, `.codex/`, `opencode.json`) unless allowed; Codex gets
  `--skip-git-repo-check` and the key as `CODEX_API_KEY` too.
- **Image hygiene** (SEC-08): every Dockerfile applies pending OS security updates; `scripts/check-image-updates.sh`
  (part of `security-scan.sh`) reports published-but-missing security updates without a vulnerability database.
- **Scaling out**: sign-in throttling shared across API replicas through Redis; two-replica test (sessions, throttling,
  live updates).
- **A2A streaming**: `SendStreamingMessage` and `SubscribeToTask` over SSE on the server; the `a2a` engine streams
  remote output live when the agent card offers it, without duplicating output on resume.
- **Launchers & deployment** (FI-01..03): `START.bat`, `STOP.bat`, `UPDATE.bat`, `RESET-DEV.bat`, `INSTALL.ps1`,
  `HEALTH-CHECK.ps1/.bat`; `install.sh`, `start.sh`, `stop.sh`, `update.sh`, `health-check.sh`; generated secrets,
  logs in `logs/`; `docker-compose.prod.yml`; Redis service; Browser Bridge image.
- **Documentation** (FI-04): the 21 documents of the specification, API reference and database schema generated from
  the code (`scripts/gen-docs.py`), release readiness report.
- **Definition-of-Done scenario** (FI-05): one Playwright run from objective to completed work with dependencies,
  parallel tasks, reviews and a critical approval, in Arabic, also verified on the Docker stack.
- **CLI execution engine** (EE-02): Claude Code, Codex CLI, Gemini CLI, OpenCode in the sandbox with verified flags;
  supported/unsupported integrations table (EE-04).
- **20-item navigation** (UX-01) with new pages: Projects, Agent Teams, Orchestration, Executions (timeline), MCP &
  A2A, Agent Runtime, Skills, Analytics (usage, cost, budgets), Settings (users).
- **Live updates** (UX-02, FND-14): Redis pub/sub after commit, WebSocket `/api/ws` with CSRF proof.
- **Observability** (UX-05): JSON logs with trace ids, OpenTelemetry spans and OTLP export, trace context carried across RabbitMQ (outbox → consumer), health of all services.
- **Automations** (UX-06): cron/interval/event triggers, history, pause/resume/edit/run/delete, in-app only.
- **Routing** (GW-07), **budgets** with approval for extra spending (GW-08), **teams** (AG-07), **existing-project
  analysis** (OR-09).
- **Browser Bridge** (BB-01..04, experimental, disabled by default) and **MCP/skills** registry, platform MCP server,
  security assessment (MCP-01..04).
- **Context & Memory** (CX-01..03, MEM-01..04): scoped, versioned, verified memory with hybrid retrieval;
  trust-separated, budgeted context assembly.
- **Agent Supervisor** (SV-01..06).
- **GitHub** integration and the development plane: per-task worktrees, protected branches, PRs with evidence,
  SHA-pinned approved merges, webhooks (GH-01..08).
- **Sandbox**, command policy, audit search, recovery workflow, circuit breakers, graceful shutdown (SEC-04..06,
  EV-03, EV-04).
- **Approval Center**, evaluation gate, performance profiles (SEC-03, OR-04, OR-08, EV-01, EV-02, EV-05).
- **Orchestration**: LangGraph planner, agent selection, bus-driven scheduler, Task Center Kanban (OR-01..07).
- **A2A v1.0** server, client and engine (A2A-01, A2A-02, EE-03); **internal bus** with outbox and idempotent
  consumers (MQ-01..04).
- **Model gateway** with Anthropic, OpenAI, OpenAI-compatible and Gemini adapters, usage and cost records,
  Providers & Models UI (GW-01..06, GW-09, EE-01).
- **Foundation**: authentication, RBAC, tenancy, encryption, audit log, agent registry and lifecycle, templates,
  dashboard, Arabic/English UI (FND-*, AG-*, SEC-01/02).

### Security
- Browser Bridge acknowledgement and enabling restricted to the Super Admin (removed from Organization Admin).
- Secrets for CLI sandboxes passed by variable name only and masked in output.

### Known limitations
See [RELEASE_READINESS.md](RELEASE_READINESS.md).
