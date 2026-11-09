# Implementation Plan — AI Agent Command Center

Spec: [`MASTER_PROMPT.md`](MASTER_PROMPT.md). Acceptance criteria and their status: [`acceptance.json`](acceptance.json).
`progress.txt` is generated from `acceptance.json` by `scripts/progress.py` — never edited by hand.

## Weighted phases (total 100%)

| # | Phase | Weight |
|---|-------|-------:|
| 1 | Project Foundation | 5 |
| 2 | UI UX Design System | 4 |
| 3 | Bilingual Frontend | 10 |
| 4 | Backend and Database | 10 |
| 5 | Agent Management | 8 |
| 6 | AI Providers | 7 |
| 7 | Manager Agent | 8 |
| 8 | LangGraph Orchestration | 8 |
| 9 | RabbitMQ Parallel Execution | 7 |
| 10 | GitHub Integration | 8 |
| 11 | Docker Sandbox | 7 |
| 12 | Approval and Security | 7 |
| 13 | Windows Launcher | 4 |
| 14 | Testing | 5 |
| 15 | Documentation and Release | 2 |

Phase % = verified criteria / total criteria in that phase. Overall % = Σ weight × phase %.
A criterion is marked done only with evidence (a passing test, a command output, a commit).

## Architecture

```
Browser (Next.js, ar/en, RTL/LTR)
   │  REST + WebSocket
   ▼
api  (FastAPI) ── PostgreSQL (SQLAlchemy/Alembic; also LangGraph checkpoints)
   │  publishes                     ▲
   ▼                                │
RabbitMQ  ──►  orchestrator (LangGraph Manager workflow)
   │                │ dispatches task messages by specialty/priority
   ▼                ▼
worker(s)  ──► Provider adapters (Anthropic / OpenAI / Mock) + Sandbox runner (Docker)
   │                                  │
   └──► git worktrees per agent/task ─┘ ──► GitHub (branches, PRs; merge only via approval)
```

Responsibilities (per spec T9/T10):
- **LangGraph** decides: plan, decompose, dependencies, review, retry, reassignment, persisted state.
- **RabbitMQ** delivers: task messages to workers, acks, retries, DLQ, priorities.
- **Backend policies** enforce: RBAC, approvals, protected branches. The frontend never is the only check.

## Repository layout

```
backend/        FastAPI app (app/), Alembic, tests, Dockerfile
frontend/       Next.js app, i18n messages (messages/ar.json, en.json)
docs/           spec, plan, acceptance criteria, architecture/security notes
scripts/        progress.py and dev helpers
START.bat STOP.bat UPDATE.bat RESET-DEV.bat   Windows launchers
docker-compose.yml  .env.example  progress.txt
```

## Engineering decisions (non-critical, documented per spec)

| Decision | Choice | Reason |
|---|---|---|
| Auth | Local username/password + JWT (HTTP-only cookie) | Single-owner platform; no external IdP needed |
| Admin bootstrap | `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env`, created on first start | No hardcoded credentials |
| Secret encryption | Fernet (AES-128-CBC + HMAC) with `APP_ENCRYPTION_KEY` from `.env` | Standard, audited library |
| Python deps | `uv` + `pyproject.toml` | Fast, lockable |
| Frontend i18n | `next-intl` | App Router support, RTL via `dir` on `<html>` |
| Live updates | WebSocket from FastAPI, fan-out of events stored in DB | Bidirectional, simple |
| LangGraph persistence | Postgres checkpointer | Survives restarts (spec rule 28) |
| GitHub auth (v1) | Fine-grained personal access token, encrypted; GitHub App as later option | Works without registering an App; App needs owner action |
| Claude access | Anthropic **API key** only. A Claude subscription is not API access | Spec T11 |
| Arena AI | Integrate only if an official API exists (see `docs/ARENA_AI.md`) | Spec T12 |

## Environment notes (development container, not the user's machine)

- Docker daemon runs, but Docker Hub is blocked by the network policy here, so images
  can't be pulled; compose files are validated with `docker compose config` and services
  are tested against locally installed PostgreSQL 16 and RabbitMQ 3.12.
- Windows `.bat` scripts can only be statically checked here; real Windows testing is required (W4).
- progress.txt is updated at every milestone during active sessions. It does not update
  when no session is running, and no background service is installed.
