#!/usr/bin/env bash
# Start the whole application (Linux/macOS). Safe to run again; never deletes data.
. "$(dirname "$0")/scripts/lib/acc.sh"
require_docker
ensure_env
say "Building and starting services (first run can take several minutes)…"
"${COMPOSE[@]}" up -d --build >>"$LOG" 2>&1 || fail "docker compose up failed"
say "Waiting for services (database migrations run automatically)…"
wait_http "http://localhost:$(port API_PORT 8000)/api/health" "API" 120
wait_http "http://localhost:$(port FRONTEND_PORT 3000)/login" "Web app" 120
health || true
URL="http://localhost:$(port FRONTEND_PORT 3000)"
say "AI Agent Command Center is running: $URL"
open_browser "$URL"
