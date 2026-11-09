# Development Handoff

Read this, then `CLAUDE.md`, then `docs/MASTER_PROMPT.md`, before continuing work.
Live status is in `PROJECT_STATUS.json` / `progress.txt` (generated) and `docs/REQUIREMENTS.md`.

## How to resume (Linux dev container)
```bash
scripts/dev-up.sh                 # local Postgres + RabbitMQ (+ Redis when added), API :8000, web :3000
cd backend && mv .env .env.local-dev; uv run pytest -q; mv .env.local-dev .env   # tests use acc_test DB
cd frontend && npm run check && CHROME_PATH=/usr/bin/google-chrome E2E_PASSWORD=dev-only-password-123 npx playwright test
python3 scripts/progress.py --check
```
Full stack in Docker (dev env, registries blocked):
`docker compose -f docker-compose.yml -f docker/offline/compose.offline.yml up -d --build` (ports 3100/8100; root `.env`).

## Conventions
- Mark progress only with evidence: `python3 scripts/progress.py --done ID="evidence" --note "..."`.
- Commit per working slice; push to `main`. Never commit `.env`.
- Owner communicates in Arabic; reply in Arabic.
- Owner-approved standing decisions: work autonomously without routine questions; Browser Bridge disabled by default with terms gate (ADR-005).

## Current state
See the bottom of `progress.txt` (UPDATE HISTORY) for the latest entry and `NEXT_ACTION`.

### Layout (spec v4, ADR-001)
- `backend/app/models/` — schema per plane (identity, agents, work, runtime, connectivity, governance); `__init__` re-exports.
- `backend/app/security/` — auth (JWT cookie + CSRF), rbac (roles/permissions as data), tenancy (`scoped`, `get_scoped`), crypto, audit.
- `backend/app/control/` — users, agents (+ templates, lifecycle). `backend/app/runtime/tools.py` — tool catalog + profile ceilings.
- `backend/app/connectivity/providers.py` — minimal list (expand in M4). `backend/app/observability/dashboard.py`.
- `frontend/src/components/providers.tsx` — QueryClient + `useUser`/`useCan`; `lib/agents.ts` hooks; `lib/toast.ts`.
- Every new page: add nav entry in `components/app-shell.tsx`, messages in `messages/{ar,en}.json`, E2E + axe in `e2e/`.

### Gotchas
- Backend tests: move `backend/.env` aside (`mv .env .env.local-dev`) so tests use `acc_test`; restore after.
- next-intl treats dots in keys as nesting — never put dotted strings as JSON keys.
- After `rm -rf .next` + build, copy `.next/static` into `.next/standalone/.next/` before `dev-up.sh` starts the web server.
- The dev DB is disposable; the super admin password in the dev container is `dev-only-password-123` (backend/.env, not committed).
