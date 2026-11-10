# Changelog

All notable changes. The project follows the milestones of the master specification (v4.0); requirement IDs refer
to `docs/requirements.json`.

## Unreleased — 2026-11-10

### Added
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
