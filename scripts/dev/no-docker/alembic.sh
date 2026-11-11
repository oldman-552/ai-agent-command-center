#!/usr/bin/env bash
# Alembic against the local development database started by infra.sh (ACC_DB selects it; default "acc").
TC=${ACC_TOOLCHAIN:-$HOME/.local/acc-toolchain}
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
export PATH="$TC/python/bin:$PATH" UV_PYTHON_DOWNLOADS=never
export DATABASE_URL="postgresql+psycopg://acc:acc_dev@localhost:5432/${ACC_DB:-acc}"
export JWT_SECRET=${JWT_SECRET:-local-development-only-jwt-secret-0123456789abcdef}
export APP_ENCRYPTION_KEY=${APP_ENCRYPTION_KEY:-bG9jYWwtZGV2LW9ubHktZmVybmV0LWtleS0wMDAwMDA=}
cd "$REPO/backend" && exec uv run --frozen alembic "$@"
