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
- The dev DB is disposable; the super admin password in the dev container is `dev-only-password-123` (backend/.env, not committed).

### Next steps (in order)
1. M10 remainder: A2A-01 (agent card + JSON-RPC task endpoint for platform agents), A2A-02 (client, tested against
   a local A2A server); an `a2a` ExecutionEngine on top of the client.
2. M6 orchestration (LangGraph planner → task DAG → dispatch → review → approval) + Task Center UI.
4. Then M13 approvals/sandbox/audit UI, M12, M7, M8, M11, M9, M5 CLI, M14, M15 (see IMPLEMENTATION_PLAN.md).
