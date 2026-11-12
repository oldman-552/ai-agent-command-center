# Development Handoff

Read this, then `CLAUDE.md`, then `docs/MASTER_PROMPT.md`, before continuing work.
Live status is in `PROJECT_STATUS.json` / `progress.txt` (generated) and `docs/REQUIREMENTS.md`.

## How to resume (Linux dev container)
```bash
scripts/dev-up.sh                 # local Postgres + RabbitMQ (user acc, vhost acc_test), API :8000, worker, web :3000
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

### Session 2026-11-10 (after 111/115) — what changed
- Tracing across RabbitMQ; A2A streaming, push notifications (SSRF-guarded webhook outbox), extended card,
  multi-turn and input-required; sign-in throttling in Redis + two-replica and nginx tests (`deploy/nginx/acc.conf`).
- **Model tool calling in the `api` engine (ADR-030)** — before it, real-model agents could not use tools.
  `gateway/toolspec.py` (offered tools), neutral history in `gateway/base.py`, per-adapter conversion in `adapters.py`,
  bounded checkpointed loop in `gateway/service.py`. Mock marker: `[[mock:call <tool> {json}]]`.
- `ask.human` → `InputRequired` → `WAITING_FOR_INPUT`; `service.answer_input`, `POST /api/executions/{id}/input`,
  question box on the execution page; handlers skip parked executions unless answered.
- Optional memory embedding model (`memory/semantic.py`, worker role `memory_index`), Memory page *Search mode* card.
- Image hygiene: `scripts/check-image-updates.sh`, every Dockerfile upgrades OS packages; offline base gains security
  suites. CLIs were installed and run in the sandbox (`docker/offline/cli.Dockerfile`); Gemini needs `--skip-trust`;
  the CLI engine refuses workspaces with CLI project config unless allowed; CLI key/model come from the agent's provider.
- Still owner-only: GW-10 (live key), FI-06 (real GitHub repo — creating one was blocked here), SEC-08 (Trivy),
  FI-01 (Windows).

### Session 2026-11-12 — owner decisions ADR-035/036
- ADR-035: a worker can drive its own visible Browser Bridge window (`agent-<identifier>` profile, signed in by hand).
- ADR-036: simplified menu (advanced tools hidden behind *Settings → Show advanced tools*, `src/lib/nav-mode.ts`;
  login lands on `/workforce/room`; E2E `login()` waits for that); the Manager may run through an Arena Local Bridge
  (`agent` route, one conversation per step) or its own window (`POST /api/workforce/manager/browser`,
  `manager._ask_window`, never typed twice); `POST/DELETE /api/workforce/workers` add (max 20) / archive workers;
  plan limit 24 tasks; room seats wrap into rows of five. `tests/wf.reset` archives extra workers and leaves windows.
- Not checked against arena.ai or a real Arena account. Workforce selectors for `arena` are guesses.

### Session 2026-11-11 — AI Workforce (M16, branch `arena/2b22bc10-ai-agent-command-center`)
- Read first: `docs/AI_WORKFORCE.md` (what it does), ADR-032/033 (why), `docs/ARENA_LOCAL_BRIDGE.md` (the bridge).
- Code: `backend/app/workforce/` (lifecycle, service, manager, scheduler, review, report, human, control, history,
  api at `/api/workforce`, `mock.py` = TEST-ONLY Manager behaviour), `backend/app/connectivity/arena_bridge/`
  (client, adapter, service, api, monitor, `probe.py`), `backend/devtools/fake_arena_bridge.py`.
  Frontend: `src/app/(app)/workforce/*`, `src/components/workforce/*`, `src/lib/workforce.ts`.
- The Arena site of the Browser Bridge is gone (ADR-032); Arena is reached only through Arena Local Bridge.
- **Not verified, owner only:** WF-20 (real bridge, four accounts), WF-21 (real Claude Manager) — `REAL-TESTS.bat`;
  WF-17 real run of the Windows scripts. WF-14 is built (ADR-034, `app/workforce/devtasks.py`): file blocks
  from text workers are applied to the task branch, tested in the sandbox and merged only after approval.
- Operations Room `/workforce/room` (the AI Workforce entry in the navigation): live map, worker screen, timeline,
  day summary, big screen, optional sounds and a reactive background, all from persisted state.
- This session had **no Docker and no Windows**. `scripts/dev/no-docker/` builds PostgreSQL, Redis, RabbitMQ, Python
  and Chromium as user processes: `toolchain.sh all`, `infra.sh up`, `test-backend.sh`, `e2e-stack.sh up`
  (see `docs/DEVELOPMENT.md`). Docker-dependent tests fail or skip there; they are not regressions.
- Gotchas:
  - The mock Manager plans numbered lines: `2. Title [skill] (after 1) {critical.operation} @W3`. Markers such as
    `[[mock:review-escalate]]` in a result steer its review (list in `app/workforce/mock.py`).
  - A critical Human-Assisted result is requested by its author (`user:<id>`), so tests need a second person to
    approve: `tests/wf.py::second_person` (an ORGANIZATION_ADMIN — never a second Super Admin).
  - `tests/conftest.py` refuses a database whose name does not end in `_test`.
  - axe-core does not test the contrast of Arabic text (ligature heuristic). Contrast defects show only in the
    English runs; keep `scripts/check-contrast.mjs` current.
  - The page title is rendered by the root layout, not by `generateMetadata` (the title vanished during
    client-side navigation and axe's `document-title` failed now and then).
  - `scripts/windows/acc.ps1` is UTF-8 **with** a byte-order mark and CRLF; call programs only through
    `Invoke-Logged` / `Invoke-Quiet` / `Invoke-Shown` (`backend/tests/test_launch_scripts.py` checks all of this).
  - After changing backend code, restart the API and the worker before the browser suite
    (`e2e-stack.sh down && e2e-stack.sh up`); after changing frontend code, rebuild and restart the web app.

### Layout (spec v4, ADR-001)
- `backend/app/models/` — schema per plane (identity, agents, work, runtime, connectivity, governance); `__init__` re-exports.
- `backend/app/security/` — auth (JWT cookie + CSRF), rbac (roles/permissions as data), tenancy (`scoped`, `get_scoped`), crypto, audit.
- `backend/app/control/` — users, agents (+ templates, lifecycle). `backend/app/runtime/tools.py` — tool catalog + profile ceilings.
- `backend/app/runtime/` — `contract.py` (engine protocol, 13-state table), `service.py` (lifecycle owner), `api.py`
  (executions + WebSocket stream), `engines/mock.py` (TEST-ONLY engine; tests bind it to 'api' via monkeypatch).
- `backend/app/connectivity/gateway/` — adapters (httpx2 stack, ADR-013), `service.py` (fallback, usage/cost, real `ApiEngine`).
- `backend/app/connectivity/providers.py` — providers/models/credentials API. `backend/app/observability/dashboard.py`.
- `frontend/src/components/providers.tsx` — QueryClient + `useUser`/`useCan`; `lib/agents.ts` hooks; `lib/toast.ts`.
- Every new page: add nav entry in `components/app-shell.tsx`, messages in `messages/{ar,en}.json`, E2E + axe in `e2e/`.

### Gotchas
- Backend tests: move `backend/.env` aside (`mv .env .env.local-dev`) so tests use `acc_test`; restore after.
- next-intl treats dots in keys as nesting — never put dotted strings as JSON keys.
- After `rm -rf .next` + build, copy `.next/static` into `.next/standalone/.next/` before `dev-up.sh` starts the web server.
- Gateway tests: inject `httpx2.AsyncClient(transport=httpx2.MockTransport(h))` into `gateway.service.HTTP_CLIENTS[kind]`.
  Anthropic SDK v2 authenticates with `Authorization: Bearer`.
- Executions are dispatched over the bus (ADR-014): API writes an outbox row, the worker (`python -m app.worker`)
  relays it and runs the execution. Tests default to `EXECUTION_DISPATCH=inline` (TestClient runs it before returning);
  `tests/test_bus.py` uses `bus` + live RabbitMQ vhost `acc_test` and starts real worker subprocesses.
- Never publish to RabbitMQ directly from request handlers — use `app.bus.outbox.enqueue(db, envelope)` before commit.
- `app/bus/`: contract (Envelope, message types), topology, outbox+relay, consumer (lock → ledger → handler → ack), handlers.
- `app/connectivity/a2a/`: `server.py` (A2A v1.0 JSON-RPC per platform agent, key-gated), `keys.py` (human-only keys API),
  `client.py` (external A2A services), `engine.py` (`a2a` engine + `a2a` provider kind). ADR-015.
- `app/orchestration/`: `planner.py` (LangGraph, schema `langgraph`), `selection.py`, `scheduler.py` (advance /
  on_execution_finished / review / recover_running), `api.py` (projects, workflows, tasks, review). ADR-016.
  The mock provider plans numbered objective lines: `2. Build API [backend] (after 1)`.
- Task Center (`frontend/src/app/(app)/tasks/page.tsx`, `lib/tasks.ts`): state lives in the query string
  (project, workflow, task, view) so every view is deep-linkable. Status color on tinted backgrounds failed axe
  contrast once — keep status color on icons or solid backgrounds.
- Tests that need an approver must use ORGANIZATION_ADMIN, not SUPER_ADMIN: the bootstrap test requires exactly one
  Super Admin and runs after test_devplane alphabetically.
- next-intl reads dots in message keys as nesting: store keys like `deploy_production`, never `deploy.production`.
- In RTL put `dir="auto"` on an inner span, not on `<li>`, or the list marker escapes the padding.
- Rows read under `with_for_update=True` also need `populate_existing=True`, or a stale identity-map copy is used.
- Tests that rebind the mock engine to 'api' must `import app.main` first, or a later import re-registers the real
  ApiEngine over the mock (this caused order-dependent failures).
- Task prompts come from `app/context/service.for_task` (scheduler.build_prompt returns prompt + manifest); the
  engine's system message is always `context.assembler.system_message()` — never put retrieved text there.
- Browser Bridge: `python -m app.browser.server` (dev-up starts it, log /tmp/acc-bridge.log). Tests drive a real headless
  Chromium against the local test page; `app.browser.client.LOCAL = Bridge(...)` runs it in-process.
- The platform MCP server is mounted at `/api/mcp/`; tests run `endpoint.running()` + httpx ASGITransport.
- The dev DB is disposable; the super admin password in the dev container is `dev-only-password-123` (backend/.env, not committed).

### Next steps (in order)
0. AI Workforce: every requirement is built. WF-20, WF-21 and the real run of WF-17 need the owner's machine (`REAL-TESTS.bat`, `HEALTH-CHECK.bat`).
1. Remaining items need the owner (see docs/RELEASE_READINESS.md): GW-10 live API call, FI-06 real provider +
   GitHub run, SEC-08 Trivy image scan, FI-01 real Windows run. Everything else is implemented and verified; keep
   docs in sync (`scripts/gen-docs.py` for API/schema).
   GitHub was only verified against an HTTP fake + local bare remote. SEC-08: image scan still never run here.
   Docker stack check: `docker/offline/` equivalents (base `acc/ubuntu-base:noble` = sandbox image with root and the
   sandbox user removed; copy uv, node and the proxy CA into docker/offline first; the frontend standalone must be
   built with API_INTERNAL_URL=http://api:8000 for the container).
   Bus test workers run with WORKER_ROLES=executions,orchestrator so the supervisor doesn't act on shared test data.
   M12 (evaluation, profiles, recovery, circuit breakers, graceful shutdown — ADR-018/020) is complete. Sandbox (`app/sandbox/`, ADR-019), audit (`/audit`), approvals (ADR-017) and the
   evaluation gate (ADR-018) are done.
   Dev sandbox image: `dockerd &` then the image `acc/sandbox:python` must exist (built offline here with
   debootstrap + `docker import`; on a normal machine `docker build -t acc/sandbox:python -f docker/sandbox/Dockerfile .`).
   Sandbox tests skip with a visible reason when Docker or the image is missing.
2. Then M13 approvals/sandbox/audit UI, M12, M7, M8, M11, M9, M5 CLI, M14, M15 (see IMPLEMENTATION_PLAN.md).
