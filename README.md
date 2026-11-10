# AI Agent Command Center

A self-hosted control plane for AI agent teams: you give the platform an objective, a manager agent plans it into
tasks, specialised agents execute them in isolated sandboxes and git worktrees, every result is evaluated, and
anything critical (merging to a protected branch, spending beyond a budget, production changes…) waits for a human
approval. Arabic (default, right-to-left) and English interfaces.

> **Status:** feature-complete against the master specification except the items that need the owner's own
> accounts or machine (a live call with your API key, a real GitHub repository run, a container image scan, a real
> Windows run). See [docs/RELEASE_READINESS.md](docs/RELEASE_READINESS.md) and `progress.txt`.

## Quick start

**Windows 10/11** — install [Docker Desktop](https://www.docker.com/products/docker-desktop/), start it, then
double-click **`START.bat`** in this folder. The first run asks for a Super Admin password, generates every other
secret into `.env`, builds the images and opens <http://localhost:3000>. See [docs/WINDOWS_SETUP.md](docs/WINDOWS_SETUP.md).

**Linux / macOS** — with Docker Engine/Desktop and Compose v2:

```bash
./install.sh      # once: checks Docker, creates .env with generated secrets, builds images
./start.sh        # starts everything and opens the browser
./health-check.sh # health of every service
./stop.sh         # stops; keeps all data
```

Then sign in as `admin` with the password you chose, add a provider under **Providers & Models** (an API key from
the provider's console — consumer chat subscriptions do not include API access), create agents, and submit an
objective from the **Tasks** page. Without a key you can explore everything with the clearly labelled **Mock**
provider.

| Script (Windows / Linux-macOS) | What it does |
|---|---|
| `START.bat` / `start.sh` | Builds if needed, starts, waits for health, opens the browser. Never deletes data. |
| `STOP.bat` / `stop.sh` | Stops all services; volumes and settings are kept. |
| `UPDATE.bat` / `update.sh` | `git pull --ff-only`, rebuild, restart; migrations run automatically. |
| `RESET-DEV.bat` | Deletes **all** data of the installation after you type `RESET`. |
| `INSTALL.ps1` / `install.sh` | First-time setup only. |
| `HEALTH-CHECK.ps1` / `.bat` / `health-check.sh` | Database, RabbitMQ, Redis, workers, Browser Bridge. |

Logs of every script run are written to `logs/`.

The first build downloads roughly 1 GB (base images and libraries). The experimental Browser Bridge is not
built unless you add `COMPOSE_PROFILES=browser-bridge` to `.env` (its Playwright image adds ~1.5 GB).

## What is inside

- **20-page web app** (Next.js, RTL-first, WCAG 2.2 AA tested): Command Center, Projects, Agents, Agent Teams,
  Tasks (Kanban), Orchestration (objectives + automations), Agent Supervisor, Executions (timeline), Browser Bridge,
  MCP & A2A, Providers & Models, Agent Runtime, Context & Memory, Skills, GitHub, Approval Center, Evaluation,
  Analytics (usage, cost, budgets), Audit Logs, Settings. Live updates over WebSocket.
- **Backend** (FastAPI, PostgreSQL 16, RabbitMQ, Redis): agent registry and lifecycle, execution runtime with
  checkpoints and recovery, LangGraph planner, bus-driven scheduler, evaluation gate, Approval Center, audit log,
  Docker sandbox, per-task git worktrees, GitHub pull requests, model gateway (Anthropic, OpenAI, OpenAI-compatible,
  Google Gemini) with routing and budgets, A2A v1.0 server/client, MCP client and server, skills with security
  inspection, scoped memory with hybrid retrieval, trust-separated context assembly, supervisor, automations,
  OpenTelemetry tracing.
- **Experimental Browser Bridge** — disabled by default, per-provider terms acknowledgement required.

## Documentation

[Architecture](docs/ARCHITECTURE.md) · [Decisions (ADRs)](docs/ARCHITECTURE_DECISIONS.md) · [Security](docs/SECURITY.md) ·
[Development](docs/DEVELOPMENT.md) · [Deployment](docs/DEPLOYMENT.md) · [API reference](docs/API_REFERENCE.md) ·
[Database schema](docs/DATABASE_SCHEMA.md) · [Agent runtime](docs/AGENT_RUNTIME.md) · [Orchestration](docs/ORCHESTRATION.md) ·
[Supervisor](docs/SUPERVISOR.md) · [Context engineering](docs/CONTEXT_ENGINEERING.md) · [Memory](docs/MEMORY_SYSTEM.md) ·
[Providers](docs/PROVIDER_INTEGRATION.md) · [Browser Bridge](docs/BROWSER_BRIDGE.md) · [MCP & skills](docs/MCP_INTEGRATION.md) ·
[A2A](docs/A2A_INTEGRATION.md) · [GitHub workflows](docs/GITHUB_WORKFLOWS.md) · [Windows setup](docs/WINDOWS_SETUP.md) ·
[Troubleshooting](docs/TROUBLESHOOTING.md) · [Changelog](docs/CHANGELOG.md) · [Release readiness](docs/RELEASE_READINESS.md)

## Safety principles (enforced in the backend, not only the UI)

- Agents never merge into protected branches or approve their own work; critical operations need a human approval
  of a frozen payload, never by the requester.
- Secrets are encrypted at rest, write-only in the API, redacted from logs, prompts and outputs.
- Retrieved content and tool output are fenced as untrusted data and can never override platform policy.
- Mock output is always labelled as mock; no prices or usage are ever invented.
- Nothing is installed into your operating system; automations run inside the platform.
