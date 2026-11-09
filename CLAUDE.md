# AI Agent Command Center — Instructions for Claude

**The single source of truth for this project is [`docs/MASTER_PROMPT.md`](docs/MASTER_PROMPT.md).**
Read it in full at the start of every session before doing any work, and follow it.

## Working rules (summary — the master prompt wins on any conflict)

- Communicate with the owner in **Arabic**.
- Follow the phase sequence in the master prompt (Phase 1 → Phase 9).
- Keep `progress.txt` (UTF-8, project root) updated with **verified** progress only:
  - weighted phases totaling 100%, defined in `docs/IMPLEMENTATION_PLAN.md`;
  - append to UPDATE HISTORY, never overwrite it;
  - a feature counts as done only when its acceptance criteria are met and its tests pass.
- Never claim a test passed without running it. Document anything untested (e.g. Windows `.bat` scripts when not on Windows).
- Never commit `.env` or secrets. Never hardcode API keys.
- Mock providers must be clearly labeled as mock.
- Agents must never merge to protected branches or bypass Super Admin approval; enforce in the backend.
- No unofficial Arena AI integration (no browser automation, scraping or stored passwords).
- After each phase report: what was implemented, files changed, tests run / passed / failed, what remains, verified %.
