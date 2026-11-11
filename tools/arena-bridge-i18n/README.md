# Arena Local Bridge GUI in Arabic and English

The GUI of [Arena Local Bridge](https://github.com/Gandhara2077/arena-local-bridge) (http://127.0.0.1:20140) exists
only in Chinese. This tool adds a language switch — **العربية / English / 中文** — at the bottom right of that page.

```bash
# from the ai-agent-command-center folder; the bridge folder defaults to ~/arena-local-bridge
tools/arena-bridge-i18n/apply.sh ~/arena-local-bridge
# reload http://127.0.0.1:20140 (no restart needed)

tools/arena-bridge-i18n/apply.sh ~/arena-local-bridge --undo   # back to the original page
```

- It keeps the original page as `src/gui.html.orig` and adds one script at the end of `src/gui.html`. Applying twice
  is harmless.
- It changes only what the page **shows** (text, placeholders, tooltips, confirm dialogs). What the bridge sends to
  Arena or to clients is not touched. The chosen language is remembered in your browser.
- Before `git pull` in the bridge folder, run `--undo`; apply again after the pull. A newer bridge may add texts
  that are not in `dictionary.json` yet — they stay in Chinese.
- Verified on the bridge's v1.1.0 page (`403122c`) rendered in Chromium: no Chinese text left in English or Arabic.
  It was not tried against a running bridge with real accounts.
