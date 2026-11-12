#!/usr/bin/env bash
# Update to the latest version: pull code (fast-forward only), rebuild images, restart. Migrations run on start.
. "$(dirname "$0")/scripts/lib/acc.sh"
require_docker
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  [ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] || fail "You have local code changes; commit or stash them first."
  git -C "$ROOT" pull --ff-only >>"$LOG" 2>&1 || fail "git pull failed (see log)"
fi
PULL=(--pull); [ "${ACC_UPDATE_NO_PULL:-}" = "1" ] && PULL=()   # offline/air-gapped hosts: reuse local base images
"${COMPOSE[@]}" build "${PULL[@]}" >>"$LOG" 2>&1 || fail "image build failed"
"${COMPOSE[@]}" up -d >>"$LOG" 2>&1 || fail "restart failed"
wait_http "http://localhost:$(port API_PORT 8000)/api/health" "API" 120
if desktop_bridge_wanted; then desktop_bridge_setup; desktop_bridge_stop; desktop_bridge_start; fi  # new code, same windows
health || true
say "Updated."
