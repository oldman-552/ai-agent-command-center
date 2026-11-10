#!/usr/bin/env bash
# Show the health of every service. Exit code 0 healthy, 1 degraded or down.
. "$(dirname "$0")/scripts/lib/acc.sh"
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  "${COMPOSE[@]}" ps 2>/dev/null | tee -a "$LOG" || true
fi
health || exit 1
curl -fsS "http://localhost:$(port API_PORT 8000)/api/health" | python3 -c "import json,sys;sys.exit(0 if json.load(sys.stdin)['status']=='ok' else 1)"
