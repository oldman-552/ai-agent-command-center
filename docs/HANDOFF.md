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
