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
  `users.manage`, `credentials.manage`, `browser_bridge.manage` (the last one only by the Super Admin).
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
