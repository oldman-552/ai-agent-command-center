# Security

Everything below is enforced in the backend. The UI only hides what a user cannot do. Each control names the
tests that verify it (`backend/tests`, `frontend/e2e`).

## Identity and access

- Local accounts, passwords hashed with **argon2id**; login throttling (HTTP 429 after 5 failures for a username within 5 minutes, audited; counted in Redis so every API replica shares it, with a per-process fallback if Redis is down).
- Session: JWT in an **HttpOnly, SameSite=Lax** cookie (`Secure` when `COOKIE_SECURE=true`); a token version on the
  user invalidates all sessions on password/role changes. **CSRF double-submit**: state-changing requests must echo
  the `acc_csrf` cookie in `X-CSRF-Token`. The WebSocket requires the same value as its first message.
- **RBAC** with six roles: Super Admin, Organization Admin, Project Manager, Developer, Reviewer, Viewer. Callers can
  only grant roles whose permissions they hold (no escalation). Exactly one Super Admin is bootstrapped.
- **Human-only permissions** — never held by agents or API keys: `approvals.decide`, `security.manage`,
  `users.manage`, `credentials.manage`, `workforce.manage`, `browser_bridge.manage` and `workforce.bridge` (the last
  two only by the Super Admin).
- **Tenancy**: every query is scoped by `organization_id`; objects of another organization answer 404.
- Machine access only through scoped keys: A2A keys (listed agents only) and MCP keys (listed tools only); only a
  SHA-256 of each token is stored, the token is shown once, keys can be revoked.

## Agents cannot exceed policy

- **Approval Center**: critical operations (production deploy, destructive DB operation, public exposure, credential
  change, permission escalation, sensitive infrastructure, merge into a protected branch, irreversible operations,
  accepting a critical task, extra spending beyond a budget) create an approval with a **frozen payload hash**. Only a
  human with `approvals.decide` can decide, never on their own request; an approved operation executes exactly once,
  and only the approved payload. Pending approvals expire.
- **Protected branches**: agents write only to `agent/<agent>/task-<id>` branches in per-task worktrees; the only way
  into `main`/`master`/the default branch is the approved merge, pinned to the approved commit SHA.
- **Evaluation gate**: tasks are never completed because an agent says so — deterministic checks decide; a reviewer
  model can only withhold.
- **Tool permissions** per agent; argv allow-lists for shell tools; MCP tools need server + tool enabled, assignment
  and the `mcp:<server>:<tool>` permission.
- **Budgets** stop new work at the limit and request an approval for more spending.
- **Supervisor** acts only from a closed list of reversible actions and never above policy.

## Sandboxing

- One throw-away Docker container per command: `--network none` (the CLI engine is the only exception, it needs
  its model API), read-only root, tmpfs `/tmp`, `--cap-drop ALL`, `no-new-privileges`, non-root uid 10001,
  CPU/memory/pids/time limits, only the task worktree mounted. Nothing from the platform environment reaches the
  container; secrets for the CLI engine are passed by variable name and masked in output.
- git runs with hooks disabled, no fsmonitor/external diff/filters, scrubbed environment and fixed argv.
- The worker needs access to a Docker daemon to start sandboxes. Mounting `/var/run/docker.sock` is root-equivalent
  on that host — prefer a dedicated Docker host or rootless Docker for the sandbox (ADR-019).

## Secrets

- API keys and tokens are **encrypted at rest (Fernet, `APP_ENCRYPTION_KEY`)**, write-only through the API (only a
  hint like `…WXYZ` is returned), decrypted only at call time, never sent to model prompts.
- Redaction of known secret formats in audit logs, execution events, memories, outputs and JSON logs.
- `.env` is created locally with generated secrets and is git-ignored. Back it up: losing `APP_ENCRYPTION_KEY`
  makes stored credentials unreadable.

## Untrusted content

- Repository files, retrieved memories, tool output, web pages and remote agents are untrusted. Context assembly puts
  them in fenced `UNTRUSTED DATA` blocks with a per-request random nonce, flags instruction-like content and keeps
  the platform policy in the system message only. This mitigates prompt injection; it is not a guarantee — the
  controls above are what actually prevent damage.
- Skills and MCP servers are statically inspected before they can be enabled; HIGH risk needs an audited acceptance.
- The Browser Bridge never reads cookies, storage or credentials, never solves CAPTCHAs, and is disabled by default.

## AI Workforce and Arena Local Bridge

Tests: `test_workforce_security.py`, `test_arena_bridge_api.py`, `test_workforce_review.py`,
`test_arena_bridge_probe.py`, `e2e/workforce.spec.ts`.

- **Permissions** `workforce.read|run|work|review|manage|bridge`. Only the Super Admin holds `workforce.bridge`;
  adding a bridge or replacing its key also needs `credentials.manage`. `workforce.manage` and `workforce.bridge`
  are human-only. Another organization's workforce, tasks, results and bridges answer 404.
- **The bridge key** is encrypted at rest, write-only, shown only as its last four characters and never logged. The
  platform never calls the bridge route that returns the key (`/api/status`).
- **No Arena credential ever reaches the platform.** Passwords, cookies and sign-ins stay in Arena Local Bridge and
  your browser. The platform does not sign in, solve CAPTCHAs or work around rate limits or quotas.
- **Terms gate.** Nothing is sent to Arena through a bridge until a person acknowledges the exact statement shown
  for that bridge. The acknowledgement is audited and is revoked when the bridge's address changes.
- **Bridge addresses** must be loopback, `host.docker.internal` or a private IPv4 address; anything else is refused
  unless listed in `ARENA_BRIDGE_ALLOWED_HOSTS`. Addresses with a user name, path or query are refused.
- **Account addresses are masked** before they are stored or shown; accounts are told apart by a salted hash.
- **A worker's answer is untrusted data.** It reaches the next worker and the Manager inside a fenced block. A
  result that looks like an instruction to the platform, or like a credential, is held for a person.
- **The Manager's verdict is advice.** Fixed platform rules decide: an unmet criterion is never an approval, HTTP
  200 is never acceptance, and a critical task is completed only by a human approval of the frozen payload
  (task, version, fingerprint of the text).
- **Two people on the critical path.** When a person did the work, that person is the requester of the approval, and
  the Approval Center never lets a requester decide their own request — whatever their role. An Automatic worker's
  result is requested by the platform; agents hold no approval right. Not enforced: a person may review their own
  **non-critical** result when the Manager hands it to a person (a one-person installation could not work
  otherwise); the history and the result page show who submitted and who accepted.
- **A prompt is redacted** like every other input before it is stored or sent; the response says when that happened.
- **No blind resend.** A request whose outcome is unknown is resent only inside the bridge's replay window, with the
  same idempotency key. After that a person decides.
- **Attachments** are size- and type-limited, stored outside the web root, named by the platform, and only ever
  served as downloads.
- **Mock is labelled.** The mock Manager and the fake bridge mark every result, usage record and report they touch.
- **Owner-run tests** (`REAL-TESTS.bat`) use a separate database and reach neither the broker nor Redis of the
  installation. The test suite refuses to run against a database whose name does not end in `_test`, because it
  erases the database it uses.

Residual risks: the bridge does not name the account that answered, so the account binding is configuration, not
proof. A request the platform abandoned keeps running inside the bridge. Whoever can reach the bridge's port with
its key can use your Arena sessions — keep it on loopback.

## HTTP hardening

Strict security headers on every API response (`Content-Security-Policy: default-src 'none'`, `X-Frame-Options:
DENY`, `nosniff`, `Referrer-Policy`), no CORS (same-origin only through the web proxy), webhook HMAC verification
(`X-Hub-Signature-256`) with replay protection.

## Supply chain

`scripts/security-scan.sh` runs pip-audit (backend lockfile), npm audit (production dependencies) and Trivy for
images when installed; HIGH/CRITICAL fail the run (`--strict` also fails when a scanner is missing). It also runs
`scripts/check-image-updates.sh`, which needs no vulnerability database: it asks each image's own package manager
(with the distribution's security suite added in a throwaway container) for security updates that are published but
not installed. Every Dockerfile applies pending updates at build time (`apt-get upgrade` / `apk upgrade`).

Last run in the development container (2026-11-10): Python and npm dependencies clean; the locally built images
(Ubuntu 24.04 base, sandbox, backend, frontend, PostgreSQL, RabbitMQ, Redis equivalents) have **no pending security
updates** — before the fix they had 72, because the debootstrapped base listed no security suite. **Trivy has not
run** (its database and every OSV/distribution tracker are unreachable there), and the official Docker Hub images
were not built — run `scripts/security-scan.sh --strict` on your machine before production.

## Reporting

Report vulnerabilities privately to the repository owner; do not open public issues for them.
