# AI Agent Command Center — Instructions for Claude

**The single source of truth is [`docs/MASTER_PROMPT.md`](docs/MASTER_PROMPT.md) (spec v4.0).**
Read it in full at the start of every session, then [`docs/HANDOFF.md`](docs/HANDOFF.md) to resume.
The previous spec (v1.0) is kept in `docs/MASTER_PROMPT.v1.md` for reference only.

## Working rules (summary — the master prompt wins on any conflict)

- Communicate with the owner in **Arabic**.
- Work autonomously: no routine questions or approvals for ordinary engineering inside the workspace.
  Ask only for irreversible or out-of-boundary actions (production, spending, external exposure, destroying user data).
- Requirements live in `docs/requirements.json` (115 IDs, matrix in `docs/REQUIREMENTS.md`).
  Progress is **verified-only**: `python3 scripts/progress.py --done ID="evidence" --note "..."` regenerates
  `progress.txt`, `PROJECT_STATUS.json`, `docs/REQUIREMENTS.md`. Never edit those three by hand. Update at every milestone.
- Record significant decisions in `docs/ARCHITECTURE_DECISIONS.md`.
- Never claim a test passed without running it. Document anything untested.
- Never commit `.env` or secrets. Mock providers must be clearly labelled as mock.
- Agents never merge to protected branches or bypass Super Admin approval — enforced in the backend.
- Browser Bridge: disabled by default, per-provider terms acknowledgement required (ADR-005, owner-approved).
- Use the installed UI UX Pro Max skill (`.claude/skills/ui-ux-pro-max`) for all UI work.
