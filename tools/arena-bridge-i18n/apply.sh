#!/usr/bin/env bash
# Arabic / English for the Arena Local Bridge GUI (http://127.0.0.1:20140), whose own interface is Chinese only.
#
#   tools/arena-bridge-i18n/apply.sh [BRIDGE_DIR]          add the language switch (default: ~/arena-local-bridge)
#   tools/arena-bridge-i18n/apply.sh [BRIDGE_DIR] --undo   restore the bridge's original page
#
# What it does: keeps a copy of src/gui.html as src/gui.html.orig and adds one script at the end of the page that
# translates what the page shows. The bridge reads gui.html on every visit, so reload the page — no restart needed.
# Only the GUI's display changes; requests to Arena and to clients are untouched.
# Before `git pull` in the bridge folder run --undo, then apply again after the pull.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${HOME}/arena-local-bridge"; UNDO=0
for a in "$@"; do
  case "$a" in
    --undo) UNDO=1 ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) DIR="$a" ;;
  esac
done
GUI="$DIR/src/gui.html"; ORIG="$GUI.orig"
[ -f "$GUI" ] || { echo "Not found: $GUI — pass the folder of arena-local-bridge, e.g. $0 ~/arena-local-bridge" >&2; exit 1; }

if [ "$UNDO" = 1 ]; then
  if [ -f "$ORIG" ]; then mv -f "$ORIG" "$GUI"; echo "Restored the original page: $GUI"; else echo "Nothing to undo (no $ORIG)."; fi
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 is needed (sudo apt install -y python3)" >&2; exit 1; }
# keep the pristine page once; every later run starts from it, so applying twice is harmless
if [ ! -f "$ORIG" ]; then
  grep -q "acc-i18n" "$GUI" && { echo "$GUI already contains the language layer but $ORIG is missing; run 'git checkout src/gui.html' in $DIR first." >&2; exit 1; }
  cp "$GUI" "$ORIG"
fi
python3 - "$ORIG" "$GUI" "$HERE/i18n.js" "$HERE/dictionary.json" <<'PY'
import json, sys
orig, gui, js, dic = sys.argv[1:5]
page = open(orig, encoding="utf-8").read()
d = json.load(open(dic, encoding="utf-8"))
code = open(js, encoding="utf-8").read().replace("__ACC_DICT__", json.dumps(d, ensure_ascii=False))
code = code.replace("</script", "<\\/script")
block = "\n<!-- acc-i18n: start (added by ai-agent-command-center/tools/arena-bridge-i18n) -->\n<script>\n" + code + "\n</script>\n<!-- acc-i18n: end -->\n"
i = page.rfind("</body>")
page = page[:i] + block + page[i:] if i != -1 else page + block
open(gui, "w", encoding="utf-8").write(page)
PY
echo "Done. Reload http://127.0.0.1:20140 — the language switch (العربية / English / 中文) is at the bottom right."
echo "To remove it: $0 $DIR --undo"
