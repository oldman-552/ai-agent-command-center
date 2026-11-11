#!/usr/bin/env bash
# Backend pytest against the services started by infra.sh. Arguments are passed to pytest.
TC=${ACC_TOOLCHAIN:-$HOME/.local/acc-toolchain}
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
export PATH="$TC/python/bin:$PATH" UV_PYTHON_DOWNLOADS=never
[ -x "$TC/chromium/chrome" ] && export BROWSER_EXECUTABLE_PATH="$TC/chromium/chrome" FONTCONFIG_PATH="$TC/chromium/bin/x-fonts"
cd "$REPO/backend" && exec uv run --frozen pytest -q -p no:cacheprovider "$@"
