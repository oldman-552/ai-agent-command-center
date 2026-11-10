#!/usr/bin/env bash
# Shared logic for start.sh / stop.sh / update.sh / install.sh / health-check.sh (Linux and macOS).
# Everything runs through Docker Compose; nothing is installed on the system without your consent.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS="$ROOT/logs"; mkdir -p "$LOGS"
LOG="$LOGS/$(basename "${0%.sh}")-$(date +%Y%m%d-%H%M%S).log"
COMPOSE=(docker compose --project-directory "$ROOT" -f "$ROOT/docker-compose.yml")
[ -f "$ROOT/docker-compose.override.yml" ] && COMPOSE+=(-f "$ROOT/docker-compose.override.yml")
[ "${ACC_PROFILE:-}" = "production" ] && COMPOSE+=(-f "$ROOT/docker-compose.prod.yml")

say()  { printf '%s\n' "$*" | tee -a "$LOG"; }
fail() { printf 'ERROR: %s\n' "$*" | tee -a "$LOG" >&2; printf 'Log: %s\n' "$LOG" >&2; exit 1; }

env_get() { grep -E "^$1=" "$ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2-; }
port()    { local v; v="$(env_get "$1")"; printf '%s' "${v:-$2}"; }

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "Docker is not installed. Install Docker Desktop (macOS) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
  docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker Desktop / the docker service and try again."
  docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is missing (the 'docker compose' command). https://docs.docker.com/compose/install/"
}

rand() { # url-safe random string of N bytes
  python3 -c "import secrets,sys;print(secrets.token_urlsafe(int(sys.argv[1])))" "$1" 2>/dev/null \
    || openssl rand -base64 "$1" | tr '+/' '-_' | tr -d '=\n'
}
fernet() { python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())" 2>/dev/null \
    || openssl rand -base64 32 | tr '+/' '-_'; }

set_if_empty() { # set KEY in .env only when it is empty
  local key="$1" val="$2" f="$ROOT/.env"
  if grep -qE "^$key=$" "$f"; then
    python3 - "$f" "$key" "$val" <<'PY' 2>/dev/null || sed -i.bak "s|^$key=$|$key=$val|" "$f"
import sys
p, k, v = sys.argv[1:]
lines = open(p).read().splitlines()
open(p, "w").write("\n".join(f"{k}={v}" if l == f"{k}=" else l for l in lines) + "\n")
PY
    rm -f "$f.bak"
    return 0
  fi
  if ! grep -qE "^$key=" "$f"; then  # missing entirely (a hand-made or partial .env): add it
    [ -s "$f" ] && [ -n "$(tail -c1 "$f")" ] && echo >> "$f"
    echo "$key=$val" >> "$f"
    return 0
  fi
  return 1
}

ensure_env() {
  [ -f "$ROOT/.env.example" ] || fail ".env.example is missing — is this the project folder?"
  if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"; chmod 600 "$ROOT/.env"; say "Created .env from .env.example"
  fi
  # a hand-made or partial .env: add every setting it lacks from .env.example (existing values are kept)
  local line key added=0
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^([A-Z][A-Z0-9_]*)= ]] || continue
    key="${BASH_REMATCH[1]}"
    if ! grep -qE "^$key=" "$ROOT/.env"; then
      [ -s "$ROOT/.env" ] && [ -n "$(tail -c1 "$ROOT/.env")" ] && echo >> "$ROOT/.env"
      echo "$line" >> "$ROOT/.env"; added=$((added + 1))
    fi
  done < "$ROOT/.env.example"
  [ "$added" -gt 0 ] && say "Added $added missing settings to .env from .env.example"
  set_if_empty JWT_SECRET "$(rand 48)" && say "Generated JWT_SECRET"
  set_if_empty APP_ENCRYPTION_KEY "$(fernet)" && say "Generated APP_ENCRYPTION_KEY (back it up: losing it makes stored credentials unreadable)"
  set_if_empty POSTGRES_PASSWORD "$(rand 24)" && say "Generated POSTGRES_PASSWORD"
  set_if_empty RABBITMQ_PASSWORD "$(rand 24)" && say "Generated RABBITMQ_PASSWORD"
  if [ -z "$(env_get ADMIN_PASSWORD)" ]; then
    if [ -t 0 ]; then
      local pw=""
      while [ "${#pw}" -lt 12 ]; do read -r -s -p "Choose the Super Admin password (min 12 characters): " pw; echo; done
      set_if_empty ADMIN_PASSWORD "$pw"
    else
      local pw; pw="$(rand 18)"; set_if_empty ADMIN_PASSWORD "$pw"
      say "Generated a Super Admin password (shown once, change it after signing in): $pw"
    fi
  fi
}

wait_http() { # url, label, seconds
  local i
  for i in $(seq 1 "$3"); do curl -fsS -o /dev/null "$1" 2>/dev/null && { say "  $2: ready"; return 0; }; sleep 2; done
  fail "$2 did not become ready ($1). See: ${COMPOSE[*]} logs"
}

open_browser() {
  local url="$1"
  if command -v open >/dev/null 2>&1; then open "$url" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url" >/dev/null 2>&1 || true; fi
}

health() { # prints each service from /api/health; exit 1 when the API is down
  local api; api="http://localhost:$(port API_PORT 8000)/api/health"
  local body; body="$(curl -fsS "$api" 2>/dev/null)" || { say "API: DOWN ($api)"; return 1; }
  python3 - "$body" <<'PY' | tee -a "$LOG"
import json, sys
b = json.loads(sys.argv[1]); s = b.get("services", {})
print(f"Overall: {b.get('status')}")
for k in ("database", "rabbitmq", "redis"):
    print(f"  {k:<15} {s.get(k, b.get(k, '?'))}")
w = s.get("workers", {})
print(f"  {'workers':<15} {w.get('status')} ({len(w.get('instances', []))} running)")
print(f"  {'browser_bridge':<15} {s.get('browser_bridge', {}).get('status')} (optional, experimental)")
PY
}
