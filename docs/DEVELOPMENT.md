# Development

## Stack

Backend: Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 16, RabbitMQ (aio-pika), Redis, LangGraph,
Playwright (Browser Bridge), MCP SDK 1.x, OpenTelemetry; `uv` for dependencies, `ruff` for linting, `pytest`.
Frontend: Node 22, Next.js (standalone), next-intl, TanStack Query, Tailwind, Playwright + axe for E2E.

## Running locally without Docker (Linux dev container)

`scripts/dev-up.sh` starts PostgreSQL, RabbitMQ and Redis if needed, applies migrations, then the API (`:8000`), the
worker, the Browser Bridge (`:8100`) and the web app (`:3000`). Configuration is read from `backend/.env` (copy
`.env.example`; set `DATABASE_URL`, `RABBITMQ_URL`, `JWT_SECRET`, `APP_ENCRYPTION_KEY`, `ADMIN_PASSWORD`).

```bash
bash scripts/dev-up.sh              # or --api-only
```

On Windows and macOS use Docker (`START.bat` / `start.sh`).

## Tests

```bash
cd backend
export UV_SYSTEM_CERTS=1
uv run ruff check app tests
uv run pytest -q                    # uses database acc_test, RabbitMQ vhost acc_test, Redis db 15
```

The test suite needs PostgreSQL, RabbitMQ, Redis and a Docker daemon (sandbox tests) and a Chromium for the Browser
Bridge tests (pre-installed for Playwright). It must not see your development `backend/.env` values (move it aside
while testing — the suite sets its own environment).

```bash
cd frontend
npm run check                        # i18n completeness + hardcoded-text guard + TypeScript
node scripts/check-contrast.mjs      # WCAG contrast of the design tokens
npm run build && cp -r .next/static .next/standalone/.next/
E2E_PASSWORD=<admin password> npx playwright test   # against the running stack (ar + en, axe)
```

## Conventions

- Requirements: `docs/requirements.json`; progress is updated only with evidence:
  `python3 scripts/progress.py --done ID="evidence" --note "…"` (regenerates `progress.txt`, `PROJECT_STATUS.json`,
  `docs/REQUIREMENTS.md` — never edit those by hand).
- Significant decisions go to `docs/ARCHITECTURE_DECISIONS.md`.
- Every UI string goes through next-intl in both `messages/ar.json` and `messages/en.json`; use logical CSS
  properties (`ms-`, `pe-`, `start`), `dir="auto"` for user content, `dir="ltr"` for code/ids.
- Never claim a test passed without running it; document anything not verified.
- `scripts/gen-docs.py` regenerates `docs/API_REFERENCE.md` and `docs/DATABASE_SCHEMA.md` from the code.
- Migrations: `uv run alembic revision --autogenerate -m "…"`, review the file, `uv run alembic upgrade head`, and
  check the downgrade.

## Adding things

- **A provider**: an adapter in `connectivity/gateway/adapters.py` (stream text + usage, map errors to
  `ProviderError`/`ProviderUnavailable`), register it, add tests with `httpx.MockTransport`.
- **An execution engine**: implement `run(ctx) -> AsyncIterator[EngineEvent]`, `register_engine(kind, Engine)`.
- **A runtime tool**: `register_tool(name, fn)`; add it to the tool catalogue with a description; enforce argument
  validation inside the tool.
- **A critical operation**: add it to `OPERATIONS` and register an executor; it is then only reachable through an
  approval.
