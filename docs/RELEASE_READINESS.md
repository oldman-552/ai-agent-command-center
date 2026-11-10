# Release readiness report

Date: 2026-11-10 · Specification: master prompt v4.0 · Requirements: 115 (`docs/requirements.json`, matrix in
`docs/REQUIREMENTS.md`, live status in `progress.txt`).

## Verdict

Ready for an **owner acceptance run** on the owner's machine. Everything that can be verified without the owner's
accounts, keys and hardware has been implemented and verified with tests that ran. The remaining items are listed
below with exactly what is missing; none of them is hidden behind a mock.

## Test evidence (last full runs)

| Suite | Result | Command |
|---|---|---|
| Backend (pytest, real PostgreSQL, RabbitMQ, Redis, Docker sandbox, headless Chromium) | **275 / 275 passed** | `cd backend && uv run pytest -q` |
| Lint | clean | `uv run ruff check app tests` |
| Frontend type check + i18n guard (1 081 keys, ar = en) | clean | `npm run check` |
| Design-token contrast (WCAG) | all pass | `node scripts/check-contrast.mjs` |
| E2E (Playwright, real Chrome, Arabic + English, axe WCAG 2.2 AA) | **61 / 61 passed** | `npx playwright test` |
| Docker Compose stack (official compose file + offline image equivalents) | all services healthy; migrations; Definition-of-Done, 20-page navigation, live WebSocket and memory E2E passed against it; data kept across stop/start | see DEPLOYMENT.md |
| Dependency scan | pip-audit: no known vulnerabilities; npm audit (prod): 0 | `scripts/security-scan.sh` |
| OS security updates in images | all 7 locally built images: 0 pending (was 72 before the base fix) | `scripts/check-image-updates.sh` |

## Definition of Done (spec §5)

| Area | Status | Evidence |
|---|---|---|
| Core platform (start, auth, migrations, dashboard, APIs) | ✅ | foundation tests, Docker stack, E2E |
| Agent runtime (create, configure, execute, persist, observable errors, recovery) | ✅ | runtime/recovery tests, execution timeline E2E |
| Orchestration (decompose, assign, dependencies, parallel, consolidate, failures) | ✅ | orchestration tests, `definition-of-done.spec.ts` |
| One official API integration works | ⚠️ simulated only | adapters tested against simulated HTTP APIs; **live call with your key pending (GW-10)** |
| One CLI integration works where feasible | ⚠️ up to the provider call | all four CLIs installed and started in the real sandbox with the engine's argv and key passing; a real task needs your API key |
| Browser Bridge for one verified provider | ⚠️ local test provider only | real providers' selectors unverified by design (ADR-005/025) |
| MCP integration | ✅ | MCP client ↔ local stdio server, platform MCP server over HTTP |
| Unsupported integrations documented | ✅ | PROVIDER_INTEGRATION.md |
| Development workflow (worktrees, isolated branches, review, tests, integration) | ✅ local · ⚠️ GitHub | local bare remote + HTTP fake; **real github.com run pending (FI-06)** |
| Intelligence (retrieval, persistence, isolation, evidence-based evaluation) | ✅ | memory/context/evaluation tests |
| Security (permissions, sandbox, sensitive data, audit, approvals) | ✅ · ⚠️ images | tests; **container image scan pending (SEC-08)** |
| User experience (RTL, LTR, pages, loading/error states, no dead buttons) | ✅ | 20-page navigation E2E (no console errors, no 5xx, axe), workspace E2E |
| Delivery (docs, installation, startup scripts, test reports, limitations) | ✅ · ⚠️ Windows | 21 docs; Linux scripts run here; **Windows scripts statically checked only (FI-01)** |

## Open items (need the owner)

| ID | What is missing | How to close it |
|---|---|---|
| GW-10 | A live call with a real API key | Providers & Models → add your Anthropic/OpenAI/Gemini key → *Test connection*, run an agent |
| FI-06 | End-to-end with a real provider and a real GitHub repository | Connect a test repository with a fine-grained token, run an objective, approve the merge |
| SEC-08 | Container image scan with Trivy (its vulnerability database is unreachable from the development environment; OS-update check done, see above) | `scripts/security-scan.sh --strict` on your machine with Trivy installed |
| FI-01 | A real Windows run of the launchers | Double-click `START.bat` on Windows 10/11 with Docker Desktop |

## Known limitations

- **Browser Bridge** is experimental: only the platform's local test page is verified; Claude/ChatGPT/Gemini selectors
  are best effort and their consumer terms may prohibit automation. Disabled by default.
- **CLI engine**: flags checked against each CLI's `--help`; all four start in the real sandbox with the engine's argv
  (offline image equivalent), but none completed a task here (no keys, provider APIs blocked). The official
  `docker/cli/Dockerfile` (node base image) was not built here.
- **Sandbox access to Docker**: compose does not mount the Docker socket by default (root-equivalent); without a
  daemon the shell tools and CLI engine report "sandbox unavailable".
- **Prompt-injection defences** are mitigations; enforcement lives in permissions and approvals.
- **Memory retrieval** uses a local hashed embedding, not a semantic model; good for overlap in wording, weaker for
  paraphrases.
- **Token counts for budgets** in context assembly are estimates without a tokenizer; recorded usage comes from the
  providers.
- **A2A**: no push notifications, extended card or multi-turn follow-ups yet (streaming is supported).
- **Scaling out**: two API processes sharing PostgreSQL and Redis are tested (sessions, shared sign-in throttling,
  live updates across replicas — `tests/test_replicas.py`); workers scale out with leader election for supervisor and
  automations. A real load balancer in front of them (sticky-free, WebSocket upgrade) was not set up here.
- Windows launchers and the official container images (Docker Hub, Playwright, node) were not built in the
  development environment, which blocks registries.
