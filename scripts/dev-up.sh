#!/usr/bin/env bash
# Dev-container helper (Linux): ensure local Postgres + RabbitMQ + Redis, then API (8000), worker, bridge and web (3000).
# Not used on Windows — START.bat runs everything in Docker there.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pg_lsclusters 2>/dev/null | grep -q online || sudo pg_ctlcluster 16 main start </dev/null >/dev/null 2>&1
pgrep -f beam.smp >/dev/null || sudo -u rabbitmq bash -c 'rabbitmq-server -detached' </dev/null >/dev/null 2>&1
redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes --port 6379 --save "" --appendonly no >/dev/null
for i in $(seq 1 30); do sudo rabbitmqctl -q status >/dev/null 2>&1 && break; sleep 1; done
# dev broker user + the isolated vhost the backend tests use (idempotent)
sudo rabbitmqctl -q list_users 2>/dev/null | grep -q '^acc' || { sudo rabbitmqctl -q add_user acc acc_dev; \
  sudo rabbitmqctl -q set_permissions -p / acc '.*' '.*' '.*'; }
sudo rabbitmqctl -q list_vhosts 2>/dev/null | grep -qx acc_test || { sudo rabbitmqctl -q add_vhost acc_test; \
  sudo rabbitmqctl -q set_permissions -p acc_test acc '.*' '.*' '.*'; }
if ! curl -sf localhost:8000/api/health >/dev/null; then
  pkill -f "uvicorn app.main" 2>/dev/null
  (cd "$ROOT/backend" && UV_SYSTEM_CERTS=1 uv run alembic upgrade head </dev/null >/dev/null 2>&1)
  (cd "$ROOT/backend" && UV_SYSTEM_CERTS=1 setsid -f uv run uvicorn app.main:app --port 8000 \
     </dev/null >/tmp/acc-api.log 2>&1 3>&- 4>&- 5>&-)
fi
# worker: relays the outbox and runs executions from RabbitMQ (M10)
if ! pgrep -f "python -m app.worker" >/dev/null; then
  (cd "$ROOT/backend" && UV_SYSTEM_CERTS=1 setsid -f uv run python -m app.worker \
     </dev/null >/tmp/acc-worker.log 2>&1 3>&- 4>&- 5>&-)
fi
# Browser Bridge service (experimental; owns the browsers, localhost only)
if ! pgrep -f "app.browser.server" >/dev/null; then
  (cd "$ROOT/backend" && UV_SYSTEM_CERTS=1 setsid -f uv run python -m app.browser.server \
     </dev/null >/tmp/acc-bridge.log 2>&1 3>&- 4>&- 5>&-)
fi
if [ "${1:-}" != "--api-only" ] && ! curl -sf -o /dev/null localhost:3000/login; then
  fuser -k 3000/tcp 2>/dev/null
  (cd "$ROOT/frontend/.next/standalone" && PORT=3000 setsid -f node server.js </dev/null >/tmp/acc-web.log 2>&1)
fi
for i in $(seq 1 30); do curl -sf localhost:8000/api/health >/dev/null && break; sleep 1; done
[ "${1:-}" != "--api-only" ] && for i in $(seq 1 30); do curl -sf -o /dev/null localhost:3000/login && break; sleep 1; done
echo "api: $(curl -s localhost:8000/api/health)"; echo "web: $(curl -s -o /dev/null -w '%{http_code}' localhost:3000/login)"
