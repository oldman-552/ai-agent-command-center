# Release readiness report

Date: 2026-11-10, **updated 2026-11-11 for the AI Workforce milestone (M16)** · Specification: master prompt v4.0 ·
Requirements: 136, of which 21 are AI Workforce (`docs/requirements.json`, matrix in `docs/REQUIREMENTS.md`, live
status in `progress.txt`).

## Verdict

Ready for an **owner acceptance run** on the owner's machine. Everything that can be verified without the owner's
accounts, keys and hardware has been implemented and verified with tests that ran. The remaining items are listed
below with exactly what is missing; none of them is hidden behind a mock.

## AI Workforce (M16) — added 2026-11-11

One prompt → a Manager analyses and plans → four workers (Automatic through Arena Local Bridge, or Human-Assisted)
→ review by fixed rules → one consolidated result. Guides: [AI_WORKFORCE.md](AI_WORKFORCE.md),
[ARENA_LOCAL_BRIDGE.md](ARENA_LOCAL_BRIDGE.md). Decisions: ADR-032, ADR-033.

**Every result below is from the mock and local-integration tiers.** The Manager was the built-in mock provider
(answers written by hand, not AI). Automatic workers talked to a fake bridge written from Arena Local Bridge's
source. No request went to a real model or to Arena. The environment had **no Docker and no Windows**.

| Suite (2026-11-11) | Result | Notes |
|---|---|---|
| Backend, whole suite | **950 passed, 1 failed, 19 skipped** | The failure needs the `docker` program (`test_cli_engine.py::test_secrets_reach_the_container_by_name_only`). 15 skips need Docker, nginx or root control of the broker; the 2026-11-10 report below records them as passing, and they could not run in this environment. 4 skips are the owner-run real tiers. |
| … of which AI Workforce (12 files) | 460 passed | lifecycle 283, recovery 36, security 30, review 28, report 26, manager 17, workers 11, human 8, modes 8, execution 7, history 5, bus 1 |
| … Arena Local Bridge adapter, settings API, probe | 108 passed | 73 + 26 + 9, over real HTTP to the fake bridge |
| … launch scripts (text checks; Linux health check against stand-ins) | 34 passed | **not** a run of the Windows scripts |
| … documents (links, script names, names that must match the code) | 26 passed | |
| … the steps of the two real tiers, on mock and fake | 10 passed | says the steps work, nothing about a real model or Arena |
| Lint · migrations | clean · no new operations | `ruff check app devtools tests` · `alembic check` |
| Frontend type check + i18n guard (1 766 keys, ar = en) · contrast · build | clean | `npm run check` · `node scripts/check-contrast.mjs` · `npm run build` |
| Browser suite (Playwright, Arabic + English, axe WCAG 2.2 AA) | **74 / 74 passed** | 8 of them are `e2e/workforce.spec.ts`. axe does not test the contrast of Arabic text; see DEVELOPMENT.md |

| Requirement | Status | What is missing |
|---|---|---|
| WF-01 … WF-13, WF-15, WF-16, WF-18, WF-19 | done, on the tiers above | — |
| WF-17 Windows scripts | written; **never executed** | Run `INSTALL.bat`, `START.bat`, `HEALTH-CHECK.bat`, `STOP.bat` on Windows with Docker Desktop. `RESET.bat` on an installation you can lose. |
| WF-20 real Arena Local Bridge, four accounts | **not run** | `REAL-TESTS.bat`, choice 2 (sends 5 short prompts to Arena through your accounts) |
| WF-21 Manager on a real Claude model | **not run** | `REAL-TESTS.bat`, choice 1 (your API key; about 5 to 8 requests) |
| WF-14 development tasks from text workers | **not built** | Files from a worker's answer applied to a worktree, tests run by the platform, protected merge. Until then the result class *Verified by the platform* is always empty. |

Limits that come from Arena Local Bridge and cannot be fixed here: it does not name the account that answered, it
reports no usage, it runs one request at a time, and a request the platform abandoned keeps running inside it.

## Test evidence (2026-11-10, before the AI Workforce; environment with Docker)

| Suite | Result | Command |
|---|---|---|
| Backend (pytest, real PostgreSQL, RabbitMQ, Redis, Docker sandbox, headless Chromium) | **327 / 327 passed** | `cd backend && uv run pytest -q` |
| Lint | clean | `uv run ruff check app tests` |
| Frontend type check + i18n guard (1 081 keys, ar = en) | clean | `npm run check` |
| Design-token contrast (WCAG) | all pass | `node scripts/check-contrast.mjs` |
| E2E (Playwright, real Chrome, Arabic + English, axe WCAG 2.2 AA) | **66 / 66 passed** | `npx playwright test` |
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
| WF-17, WF-20, WF-21 | AI Workforce on your machine | See the AI Workforce section above |

## Known limitations

- **Browser Bridge** is experimental: only the platform's local test page is verified; Claude/ChatGPT/Gemini selectors
  are best effort and their consumer terms may prohibit automation. Disabled by default.
- **CLI engine**: flags checked against each CLI's `--help`; all four start in the real sandbox with the engine's argv
  (offline image equivalent), but none completed a task here (no keys, provider APIs blocked). The official
  `docker/cli/Dockerfile` (node base image) was not built here.
- **Sandbox access to Docker**: compose does not mount the Docker socket by default (root-equivalent); without a
  daemon the shell tools and CLI engine report "sandbox unavailable".
- **Tool calling with real models** (fixed 2026-11-10: before, API-engine agents could not call tools at all) is
  verified with simulated provider streams and the mock model only; the first live check comes with your API key.
- **Prompt-injection defences** are mitigations; enforcement lives in permissions and approvals.
- **Memory retrieval** uses a local hashed embedding by default (good for overlap in wording, weaker for paraphrases);
  an OpenAI/OpenAI-compatible embedding model can be added and was tested only against a simulated provider.
- **Token counts for budgets** in context assembly are estimates without a tokenizer; recorded usage comes from the
  providers.
- **A2A**: text parts only (streaming, push notifications, extended card, multi-turn and input-required are
  supported); push webhooks have a DNS-rebinding residual risk (see A2A_INTEGRATION.md).
- **Scaling out**: two API processes sharing PostgreSQL and Redis are tested (sessions, shared sign-in throttling,
  live updates across replicas — `tests/test_replicas.py`); workers scale out with leader election for supervisor and
  automations. Also tested behind a real nginx 1.24 built from the shipped `deploy/nginx/acc.conf` (balanced
  requests on one session, shared throttling, WebSocket upgrade with live updates); TLS in that file is only
  syntax-checked (`nginx -t`), not served to a browser here.
- Windows launchers and the official container images (Docker Hub, Playwright, node) were not built in the
  development environment, which blocks registries.
