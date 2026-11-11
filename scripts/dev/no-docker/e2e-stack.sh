#!/usr/bin/env bash
# The whole platform as plain user processes for the browser E2E suite, on a machine without Docker:
# API (:8000), worker, Browser Bridge service (:8100), the FAKE Arena Local Bridge (:20140, tests only) and the web
# app (:3000). Uses its own database (acc_e2e), broker vhost (acc_e2e) and Redis db (2), all started by infra.sh.
#
#   e2e-stack.sh up | down | status | env
#
# After `up`:   cd frontend && eval "$(../scripts/dev/no-docker/e2e-stack.sh env)" && npx playwright test
# The web app is built with `npm run build` first if frontend/.next/standalone is missing (set E2E_REBUILD=1 to force).
# Development and test data only; every password here is a fixed, public, local value.
set -u
TC=${ACC_TOOLCHAIN:-$HOME/.local/acc-toolchain}
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
RUN=$TC/run/e2e
DATA=${E2E_DATA_DIR:-/tmp/acc-e2e}
PY=$REPO/backend/.venv/bin/python
ADMIN_PW=${E2E_PASSWORD:-dev-only-password-123}
FAKE_KEY=${E2E_FAKE_BRIDGE_KEY:-local-dev-key-0123456789}
mkdir -p "$RUN" "$DATA/workspaces" "$DATA/attachments" "$DATA/browser-profiles" "$DATA/skills"

# e2e/integrations.spec.ts inspects and installs one skill from SKILLS_DIR; the repository ships none there. This is a
# text-only fixture: the scanner reads it, nothing in it is ever executed.
seed_skill() {
  local dir="$DATA/skills/e2e-demo-skill"
  [ -f "$dir/SKILL.md" ] && return 0
  mkdir -p "$dir"
  cat > "$dir/SKILL.md" <<'SKILL'
---
name: e2e-demo-skill
description: TEST FIXTURE for the browser E2E suite. Not a real skill; it exists so the skill scanner has something to flag.
---
# E2E demo skill (test fixture)

This file only exists so that the "inspect before install" screen has a finding to show. The line below is text that
the static scanner classifies as a dangerous command. Nothing here is run by the platform.

    rm -rf ./build
SKILL
}

export DATABASE_URL="postgresql+psycopg://acc:acc_dev@localhost:5432/acc_e2e"
export RABBITMQ_URL="amqp://acc:acc_dev@localhost:5672/acc_e2e"
export REDIS_URL="redis://localhost:6379/2"
export JWT_SECRET="local-development-only-jwt-secret-0123456789abcdef"
export APP_ENCRYPTION_KEY="bG9jYWwtZGV2LW9ubHktZmVybmV0LWtleS0wMDAwMDA="
export ADMIN_USERNAME=admin ADMIN_PASSWORD="$ADMIN_PW"
export EXECUTION_DISPATCH=bus LOG_FORMAT=text PYTHONUNBUFFERED=1
export WORKSPACES_DIR="$DATA/workspaces" WORKFORCE_ATTACHMENTS_DIR="$DATA/attachments"
export BROWSER_PROFILES_DIR="$DATA/browser-profiles" SKILLS_DIR="$DATA/skills"
export BROWSER_SHOW_TEST_PAGE=true ARENA_BRIDGE_HEALTH_INTERVAL_SECONDS=5
[ -x "$TC/chromium/chrome" ] && export BROWSER_EXECUTABLE_PATH="$TC/chromium/chrome" FONTCONFIG_PATH="$TC/chromium/bin/x-fonts"

up_port() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
alive() { [ -s "$RUN/$1.pid" ] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

# start NAME DIR COMMAND… — detached, own session, output to a log file, pid kept for `down`
start() {
  local name=$1 dir=$2; shift 2
  alive "$name" && return 0
  ( cd "$dir" && exec setsid "$@" </dev/null >"$RUN/$name.log" 2>&1 ) &
  echo $! > "$RUN/$name.pid"
}

wait_port() {
  local port=$1 name=$2 i
  for i in $(seq 1 "${3:-60}"); do up_port "$port" && return 0; sleep 1; done
  echo "$name: did not start (see $RUN/$name.log)"; tail -5 "$RUN/$name.log" 2>/dev/null; return 1
}

stop() {
  local name=$1 pid
  [ -s "$RUN/$name.pid" ] || return 0
  pid=$(cat "$RUN/$name.pid")
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -KILL -- "-$pid" 2>/dev/null || true
  rm -f "$RUN/$name.pid"
}

case "${1:-status}" in
  up)
    "$REPO/scripts/dev/no-docker/infra.sh" up >/dev/null
    seed_skill
    ( cd "$REPO/backend" && "$PY" -m alembic upgrade head >"$RUN/migrate.log" 2>&1 ) || { echo "migrations failed:"; tail -5 "$RUN/migrate.log"; exit 1; }
    start api "$REPO/backend" "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
    start worker "$REPO/backend" "$PY" -m app.worker
    start browser-bridge "$REPO/backend" "$PY" -m app.browser.server
    start fake-arena-bridge "$REPO/backend" "$PY" -m devtools.fake_arena_bridge --port 20140 --key "$FAKE_KEY"
    wait_port 8000 api || exit 1
    wait_port 20140 fake-arena-bridge 20 || exit 1
    if [ "${E2E_NO_WEB:-}" != "1" ]; then
      if [ ! -f "$REPO/frontend/.next/standalone/server.js" ] || [ "${E2E_REBUILD:-}" = "1" ]; then
        echo "building the web app…"
        ( cd "$REPO/frontend" && npm run build >"$RUN/build.log" 2>&1 ) || { echo "build failed:"; tail -20 "$RUN/build.log"; exit 1; }
      fi
      rm -rf "$REPO/frontend/.next/standalone/.next/static"
      cp -r "$REPO/frontend/.next/static" "$REPO/frontend/.next/standalone/.next/static"
      [ -d "$REPO/frontend/public" ] && cp -r "$REPO/frontend/public" "$REPO/frontend/.next/standalone/"
      PORT=3000 HOSTNAME=0.0.0.0 API_INTERNAL_URL=http://127.0.0.1:8000 start web "$REPO/frontend/.next/standalone" node server.js
      wait_port 3000 web || exit 1
    fi
    "$0" status
    ;;
  down)
    for s in web fake-arena-bridge browser-bridge worker api; do stop "$s"; done
    echo "stopped"
    ;;
  status)
    for s in api:8000 worker: browser-bridge:8100 fake-arena-bridge:20140 web:3000; do
      name=${s%%:*} port=${s##*:}
      if alive "$name"; then
        if [ -z "$port" ] || up_port "$port"; then echo "$name: up"; else echo "$name: starting"; fi
      else echo "$name: down"; fi
    done
    ;;
  env)  # what the Playwright suite needs
    echo "export E2E_PASSWORD='$ADMIN_PW' E2E_FAKE_BRIDGE_URL='http://127.0.0.1:20140' E2E_FAKE_BRIDGE_KEY='$FAKE_KEY'"
    [ -x "$TC/chromium/chrome" ] && echo "export CHROME_PATH='$TC/chromium/chrome' FONTCONFIG_PATH='$TC/chromium/bin/x-fonts'"
    ;;
  *) echo "usage: $0 up|down|status|env"; exit 2 ;;
esac
