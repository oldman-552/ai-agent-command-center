#!/usr/bin/env bash
# Stop all services. Keeps every volume (database, repositories, workspaces) and your configuration.
. "$(dirname "$0")/scripts/lib/acc.sh"
require_docker
desktop_bridge_stop
"${COMPOSE[@]}" stop >>"$LOG" 2>&1 || fail "docker compose stop failed"
say "Stopped. Your data is kept; run ./start.sh to start again."
