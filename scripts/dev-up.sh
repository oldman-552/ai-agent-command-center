#!/usr/bin/env bash
# Dev-container helper (Linux): ensure local Postgres + RabbitMQ, then API (8000) and web (3000).
# Not used on Windows — START.bat runs everything in Docker there.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pg_lsclusters 2>/dev/null | grep -q online || sudo pg_ctlcluster 16 main start
pgrep -f beam.smp >/dev/null || sudo -u rabbitmq bash -c 'rabbitmq-server -detached' >/dev/null 2>&1
for i in $(seq 1 30); do sudo rabbitmqctl -q status >/dev/null 2>&1 && break; sleep 1; done
if ! curl -sf localhost:8000/api/health >/dev/null; then
  pkill -f "uvicorn app.main" 2>/dev/null
  (cd "$ROOT/backend" && UV_SYSTEM_CERTS=1 uv run alembic upgrade head >/dev/null 2>&1; \
   UV_SYSTEM_CERTS=1 setsid nohup uv run uvicorn app.main:app --port 8000 </dev/null > /tmp/acc-api.log 2>&1 &)
fi
if [ "${1:-}" != "--api-only" ] && ! curl -sf -o /dev/null localhost:3000/login; then
  fuser -k 3000/tcp 2>/dev/null
  (cd "$ROOT/frontend/.next/standalone" && PORT=3000 setsid nohup node server.js </dev/null > /tmp/acc-web.log 2>&1 &)
fi
for i in $(seq 1 30); do curl -sf localhost:8000/api/health >/dev/null && break; sleep 1; done
echo "api: $(curl -s localhost:8000/api/health)"; echo "web: $(curl -s -o /dev/null -w '%{http_code}' localhost:3000/login)"
