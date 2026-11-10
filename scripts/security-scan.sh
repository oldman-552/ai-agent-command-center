#!/usr/bin/env bash
# Dependency + container vulnerability scanning (SEC-08). Exit code: 0 clean, 1 findings, 2 a required scanner missing.
#
#   scripts/security-scan.sh            # Python deps (pip-audit), npm deps (npm audit), images (Trivy if installed)
#   scripts/security-scan.sh --strict   # also fail when a scanner is unavailable (use in CI)
#
# Images scanned: acc/sandbox:python and any image built from docker-compose.yml (acc-backend, acc-frontend).
# Severity gate: HIGH and CRITICAL fail the run; lower severities are reported only.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STRICT=0; [ "${1:-}" = "--strict" ] && STRICT=1
status=0; missing=0
section() { printf '\n== %s ==\n' "$1"; }

section "Python dependencies (pip-audit, backend/uv.lock)"
if command -v uv >/dev/null; then
  req="$(mktemp)"
  (cd "$ROOT/backend" && uv export --frozen --no-hashes --no-dev --format requirements-txt >"$req" 2>/dev/null)
  if uvx --from pip-audit pip-audit -r "$req" --disable-pip --no-deps --progress-spinner off 2>/dev/null; then
    echo "OK: no known vulnerabilities"
  else
    echo "FINDINGS in Python dependencies"; status=1
  fi
  rm -f "$req"
else
  echo "SKIPPED: uv not installed"; missing=1
fi

section "npm dependencies (npm audit, production deps, high+)"
if command -v npm >/dev/null; then
  if (cd "$ROOT/frontend" && npm audit --omit=dev --audit-level=high); then
    echo "OK: no high/critical vulnerabilities"
  else
    echo "FINDINGS in npm dependencies"; status=1
  fi
else
  echo "SKIPPED: npm not installed"; missing=1
fi

section "Container images (Trivy, HIGH/CRITICAL)"
if command -v trivy >/dev/null && command -v docker >/dev/null; then
  images=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(acc/sandbox|acc-backend|acc-frontend|ai-agent-command-center-(backend|frontend|worker))' || true)
  if [ -z "$images" ]; then
    echo "SKIPPED: no project images built yet"; missing=1
  fi
  for img in $images; do
    echo "-- $img"
    trivy image --quiet --severity HIGH,CRITICAL --exit-code 1 --ignore-unfixed "$img" || status=1
  done
else
  echo "SKIPPED: trivy (https://github.blesdmm.dns-dynamic.net/aquasecurity/trivy) or docker not installed"; missing=1
fi

if [ "$status" -ne 0 ]; then echo; echo "RESULT: vulnerabilities found"; exit 1; fi
if [ "$missing" -ne 0 ] && [ "$STRICT" -eq 1 ]; then echo; echo "RESULT: a scanner was unavailable (--strict)"; exit 2; fi
echo; echo "RESULT: clean$([ "$missing" -ne 0 ] && echo ' (some scanners skipped — see above)')"
