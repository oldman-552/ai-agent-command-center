#!/usr/bin/env bash
# First-time setup: checks Docker, creates .env with generated secrets, builds the images. Installs nothing else.
. "$(dirname "$0")/scripts/lib/acc.sh"
require_docker
ensure_env
say "Building images…"
"${COMPOSE[@]}" build >>"$LOG" 2>&1 || fail "image build failed"
if docker image inspect acc/sandbox:python >/dev/null 2>&1; then say "Sandbox image present."
else
  say "Building the agent sandbox image (acc/sandbox:python)…"
  docker build -t acc/sandbox:python "$ROOT/docker/sandbox" >>"$LOG" 2>&1 || say "WARNING: sandbox image build failed — shell tools will be unavailable (see log)"
fi
say "Installed. Run ./start.sh"
