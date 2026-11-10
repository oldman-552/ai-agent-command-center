#!/usr/bin/env bash
# Runs the Browser Bridge on this computer with visible Chrome windows (desktop mode).
#   1st time: ./bridge-desktop.sh --setup   (installs Python deps + Chromium for the bridge, enables desktop mode)
#   then:     ./start.sh  and, in another terminal, ./bridge-desktop.sh   (keep it open while you use the bridge)
# Each browser profile (one per employee, agent or account) opens in its own window; profiles live in
# ./browser-profiles on this computer. Needs a desktop session (a screen) — not for servers or Codespaces.
. "$(dirname "$0")/scripts/lib/acc.sh"
set -e
cd "$ROOT"
[ -f .env ] || fail "run ./install.sh first"
if [ "${1:-}" = "--setup" ]; then
  command -v uv >/dev/null 2>&1 || { say "Installing uv (Python package manager)…"; curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; }
  say "Installing the bridge's Python packages…"
  (cd backend && uv sync --frozen --no-dev >>"$LOG" 2>&1) || fail "uv sync failed (see $LOG)"
  say "Installing Chromium for the bridge (about 150 MB)…"
  (cd backend && uv run playwright install --with-deps chromium >>"$LOG" 2>&1) || fail "playwright install failed (see $LOG)"
  grep -q '^ACC_DESKTOP_BRIDGE=' .env || echo "ACC_DESKTOP_BRIDGE=1" >> .env
  say "Desktop mode enabled. Restart the platform (./stop.sh && ./start.sh), then run ./bridge-desktop.sh"
  exit 0
fi
[ "$(env_get ACC_DESKTOP_BRIDGE)" = "1" ] || fail "desktop mode is off: run ./bridge-desktop.sh --setup first"
[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || fail "no screen found (DISPLAY/WAYLAND_DISPLAY): run this in your desktop session"
GW="$(docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true)"
[ -n "$GW" ] || GW=172.17.0.1
mkdir -p "$ROOT/browser-profiles" && chmod 700 "$ROOT/browser-profiles"
set -a; . "$ROOT/.env"; set +a
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${BRIDGE_DB_PORT:-55432}/${POSTGRES_DB}"
export REDIS_URL="redis://127.0.0.1:${BRIDGE_REDIS_PORT:-56379}/0"
export BROWSER_PROFILES_DIR="$ROOT/browser-profiles" BROWSER_HEADLESS=false
export BROWSER_BRIDGE_URL="http://127.0.0.1:${BRIDGE_PORT:-8100}" BROWSER_BRIDGE_BIND="$GW"
export PLATFORM_INTERNAL_URL="http://127.0.0.1:${API_PORT:-8000}"
say "Browser Bridge on this computer (windows visible), listening on $GW:${BRIDGE_PORT:-8100} for the platform."
say "Keep this terminal open; Ctrl+C stops the bridge."
cd backend && exec uv run python -m app.browser.server
