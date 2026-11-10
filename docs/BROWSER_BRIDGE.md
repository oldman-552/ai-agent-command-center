# Browser Bridge (EXPERIMENTAL)

The Browser Bridge lets authorized agents use AI chat websites inside **dedicated, isolated** browser profiles.
It is **disabled by default**, every third-party provider stays **locked** until the Super Admin acknowledges that
provider's terms, and the official APIs and CLIs remain the recommended way to reach a model (ADR-005, ADR-025).

## 1. Reference evaluation — `YosefHayim/ai-browser-bridge` (BB-01)

Evaluated 2026-11-10 from the public GitHub page (the code was **not** copied or vendored).

| Aspect | Finding | Consequence for us |
|---|---|---|
| License | MIT | Reuse would be allowed with attribution; we chose to re-implement instead (below). |
| Language / runtime | TypeScript, Node.js ≥ 22, pnpm, Ink CLI, Playwright, MCP SDK | Our backend is Python; a second runtime only for this would add an install burden on Windows. |
| Platforms | **macOS only** (relies on `open`, `pbcopy`, `lsof`) | Spec requires Windows/Linux/macOS — the reference cannot be reused as is. |
| Browser control | Playwright over CDP, attaches to Chrome on a debug port | We launch our own Chromium per session (`launch_persistent_context`) — no debug port is opened. |
| Profiles / auth | One shared profile in `~/.ai-browser-bridge/chrome-profile`; the user signs in once | We use one profile directory **per organization, provider and profile name** under `BROWSER_PROFILES_DIR`, never a personal profile. |
| Providers | ChatGPT (default), Gemini, Claude, DeepSeek, Grok, Perplexity, Duck.ai, Arena, Google Flow; non-ChatGPT/Gemini selectors "best-effort, need live verification" | Our real adapters (Claude, ChatGPT, Gemini) are likewise **unverified** and labelled so. |
| MCP | Inbound tools for ChatGPT/Claude/Grok via a **Cloudflare tunnel** that publishes a local MCP server on a public URL; outbound `ask`, `flow_*` tools | We never publish anything to the internet: our MCP server is part of the platform API and needs a scoped access key. |
| Security posture | repo-root path validation, allow-listed test commands, permission modes, confirmations for destructive Flow ops | Similar ideas, enforced server-side: gate checks on every start/prompt, audit log, no cookie access at all. |
| Maintenance signal | 2 stars, 0 forks, 12 open issues, 183 commits; last-commit date not visible | Too early to depend on. |
| Terms of service | Automating consumer chat sites is commonly prohibited by provider terms | Hence disabled by default + per-provider terms acknowledgement + EXPERIMENTAL labels. |

**Decision:** re-implement a small, cross-platform bridge in Python with Playwright (ADR-025), keeping the useful ideas
(session state machine, MCP tool names from the spec, selector-based adapters) and none of the network exposure.

## 2. What it does

- Session states (spec Phase 11): `DISCONNECTED → STARTING → AUTH_REQUIRED | READY → SENDING → GENERATING →
  COMPLETED → SENDING …`, plus `FAILED` and `STOPPED`. Transitions are enforced by a table (`app/browser/bridge.py`).
- Capabilities: start/stop, sign-in / CAPTCHA detection (waits for a human), conversation creation and resumption,
  prompt submission, streaming of the visible answer, generation monitoring, cancellation, timeouts, reconnection,
  error reporting, health.
- **Sites** (`app/browser/adapters.py`, owner decision 2026-11-10): only **arena.ai** (`arena`) is built in; the
  Claude/ChatGPT/Gemini adapters were removed. The Super Admin adds any other website with **Add site** (name,
  https address, terms address, optional selectors; `POST /api/browser-bridge/sites`, audited). Every site stays
  locked until its terms are acknowledged; removing a site also removes its acknowledgement and selector overrides.
  The local test page (`test`) is listed only with `BROWSER_SHOW_TEST_PAGE=true` (development and tests).
- **arena.ai:** its terms of use prohibit accessing the service by automated means (§5(vi)) and share inputs with
  third-party model providers that may not keep them confidential (§3.4); they also forbid personal or sensitive
  data (§3.5(F)). Its acknowledgement statement names those clauses, and the generic statement does not unlock it.
  Its selectors are guesses never checked against the live page; its bot checks stop a session in
  `AUTH_REQUIRED` for a person to solve — the bridge never touches a challenge.
- **Browser profiles — one per employee, agent or account** (`GET/POST/DELETE /api/browser-bridge/profiles`,
  *Browser profiles* on the page). A profile is one browser profile directory used for every site, so a person's
  sign-ins (e.g. their Arena account) live in it. A person's own profile (their username) and an agent's
  (`agent-<identifier>`) are created the first time they open a session; accounts not tied to a person are added
  by name. A profile opens in **one window at a time**; deleting it deletes its stored sign-ins on the bridge
  machine. Profile directories never point at a personal browser profile.
- **Selectors** (Super Admin, audited, `PUT /api/browser-bridge/providers/{key}/selectors`; *Selectors* on the
  provider card): when a provider's page changes, its CSS selectors can be overridden per organization without a
  code change; they apply from the next start of a session, and an overridden provider is always *unverified*.
- Execution engine `browser`: an agent whose `execution_engine` is `browser` and whose
  `context_policy.browser_session_id` names a session runs its turns through the bridge.
- MCP tools (on the platform MCP server, key-scoped): `list_providers`, `get_provider_status`,
  `list_available_models`, `create_browser_session`, `close_browser_session`, `create_conversation`,
  `resume_conversation`, `send_prompt`, `get_generation_status`, `get_conversation_history`, `cancel_generation`,
  `get_bridge_health`.

## 3. Security controls (BB-03, BB-04)

- Disabled by default; enabling/disabling, acknowledging and revoking terms need `browser_bridge.manage`, a
  human-only permission held only by the Super Admin. Each action is audited. Disabling stops all live sessions.
- The acknowledgement must be the exact statement shown for that provider; it is stored with who and when.
- The gate is checked by the API **and** again by the bridge service on every start and every prompt.
- Profiles: names match `^[a-z0-9][a-z0-9-]{0,39}$` and resolve inside `BROWSER_PROFILES_DIR/<org>/<provider>/`;
  one live session per profile.
- No cookies, storage or credentials: the bridge code has no call that reads them (a test asserts this on the source),
  only the visible answer text is stored, and that text is secret-redacted.
- CAPTCHAs and sign-in pages put the session in `AUTH_REQUIRED`; the bridge never interacts with them.
- If expected page elements are missing, the session fails with `interface_changed` instead of guessing.
- The bridge service listens on localhost (or the private compose network) and requires an internal token derived
  from the platform secret.

## 4. Running it

**Desktop mode — watch the windows yourself (Linux desktop).** The bridge runs on your computer with visible Chrome
windows while the rest stays in Docker:
```bash
./bridge-desktop.sh --setup     # once: Python packages + Chromium for the bridge, enables desktop mode in .env
./stop.sh && ./start.sh         # restart so the API and worker use the bridge on this computer
./bridge-desktop.sh             # keep this terminal open while you use the bridge
```
Each session opens its profile in its own window: sign in to Arena once per profile (one profile per employee),
then open any profile's session to follow it. Profiles are stored in `./browser-profiles` (mode 700). The bridge
listens only on Docker's host-gateway address, never on all interfaces; PostgreSQL and Redis are published on
`127.0.0.1` only (`docker-compose.desktop.yml`). Needs a screen: not for servers or Codespaces. If a firewall
(ufw) is on, allow the Docker networks to reach port 8100 on the gateway address.

- Development: `scripts/dev-up.sh` starts `python -m app.browser.server` on `127.0.0.1:8100`.
- Docker: the `browser-bridge` service (`backend/Dockerfile.bridge`, Playwright image, headless). Not published to the host.
- Real providers need a **visible** browser window so a person can sign in: run the bridge on the host with
  `BROWSER_HEADLESS=false`. This has **not** been verified against the real sites (no accounts were used).

## 5. Verification status

| Item | Status |
|---|---|
| State machine, streaming, cancel, stop/restart, sign-in, CAPTCHA, interface change, session limit, gate re-check, no secret leakage | `backend/tests/test_browser.py` against the local test page with real headless Chromium |
| `browser` execution engine | `test_browser_engine_runs_an_agent_turn` |
| UI (gate, terms dialog, live session, axe) | `frontend/e2e/browser-bridge.spec.ts` |
| Sites added, profiles per person (shared across sites, one window each), deleting a profile's sign-ins | `test_browser.py`, `browser-bridge.spec.ts` |
| arena.ai and sites you add | **Not verified** — selectors are best effort; adjust them under *Selectors* |
| Desktop mode (`./bridge-desktop.sh`, visible windows) | Container → host-gateway reachability checked; **visible windows not run here** (no screen) |
| Windows / macOS | **Not verified** here (Linux only); Playwright supports both |
