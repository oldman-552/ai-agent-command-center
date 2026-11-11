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

What the browser suite needs from the stack it runs against:

- `E2E_FAKE_BRIDGE_URL` and `E2E_FAKE_BRIDGE_KEY` — the address and key of a running **fake** Arena Local Bridge
  (`python -m devtools.fake_arena_bridge --port 20140 --key <key>` in `backend/`), reachable from the API. Without
  them the two bridge tests of `e2e/workforce.spec.ts` are skipped. Never point these at a real bridge.
- A worker process (the workforce plans and reviews in the worker) and the built-in mock provider.
- One skill folder with a `SKILL.md` under `SKILLS_DIR` for `e2e/integrations.spec.ts` (inspect → install). The
  repository ships none there.
- `BROWSER_SHOW_TEST_PAGE=true` and a running Browser Bridge service for `e2e/browser-bridge.spec.ts`.

**What the accessibility checks do not cover.** The suite runs axe-core on every view in Arabic and in English.
axe-core does not test the colour contrast of text it takes for an icon ligature, and joined Arabic letters match
that heuristic (`isIconLigature`, axe-core 4.14): on an Arabic page the `color-contrast` rule silently skips the
Arabic text, and when the first three text nodes of a font family look like that, all later text in that family —
Latin words included. So contrast is effectively checked by the **English** run — the components
and colours are the same in both languages — and by `scripts/check-contrast.mjs`, which is independent of language.
Keep both: a state that only appears in the Arabic run has no automated contrast check. The other axe rules (names,
labels, roles, landmarks, focus order) do run on the Arabic pages.

### Without Docker

`scripts/dev/no-docker/` runs everything as plain user processes on a Linux machine that has neither Docker nor root
(developer convenience; the installers do not use it):

```bash
scripts/dev/no-docker/toolchain.sh all     # builds Python, PostgreSQL, Redis, Erlang + RabbitMQ, Chromium (once)
scripts/dev/no-docker/infra.sh up          # PostgreSQL, Redis, RabbitMQ
scripts/dev/no-docker/test-backend.sh      # the backend suite (tests that need a Docker daemon skip or fail)
scripts/dev/no-docker/e2e-stack.sh up      # API, worker, Browser Bridge, FAKE Arena bridge, web app (own database)
cd frontend && eval "$(../scripts/dev/no-docker/e2e-stack.sh env)" && npx playwright test
scripts/dev/no-docker/e2e-stack.sh down
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
