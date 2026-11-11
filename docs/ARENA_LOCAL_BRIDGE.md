# Arena Local Bridge integration

How the platform reaches Arena for **Automatic** AI Workforce workers. Decision record: ADR-032. Source assessment:
[`WORKFORCE_ASSESSMENT.md`](WORKFORCE_ASSESSMENT.md).

> **Read this first**
> - Arena Local Bridge (ALB, `Gandhara2077/arena-local-bridge`) is a separate program that you install and run on
>   your own computer. It drives arena.ai in a signed-in browser. It is **not an official Arena API**, and Arena's
>   terms of use apply to you when you use it.
> - The platform never signs in to Arena, never sees an Arena password or cookie, and never solves or bypasses
>   CAPTCHAs, rate limits or quotas. All of that stays inside ALB and your browser.
> - You do not need ALB at all for **Human-Assisted** workers. That is the default mode.
> - Everything in this document was built and tested against a **fake bridge** that reproduces ALB's documented
>   behaviour. A real ALB with real Arena accounts was **not** run during development. See
>   [What is not verified](#what-is-not-verified).

---

## 1. Set it up

1. Install and start ALB on the host (Windows: `ArenaLocalBridge.exe` or `start-gui.bat`). Sign in to your Arena
   account(s) in its window. Note its port (default `20140`) and its key (`ARENA_AGENT_BRIDGE_KEY`).
2. In ALB, create one conversation ("session") per account that a worker should use.
3. In the platform, open **AI Workforce → Arena Local Bridge** (`/workforce/bridge`) as the Super Admin.
4. Add a bridge:
   - **Address** — `http://host.docker.internal:20140` when the platform runs in Docker (the default install);
     `http://127.0.0.1:20140` when the backend runs directly on the host.
   - **Key** — the ALB key. It is encrypted and never shown again; only its last four characters are displayed.
5. Press **Test connection**. Every step should be green up to "terms". See [Diagnostics](#3-diagnostics).
6. Read the statement on the page and **acknowledge the terms**. Until you do, no chat request leaves the platform.
7. Press **Discover sessions**, then give each Automatic worker one session (AI Workforce → Workers).

Do not paste the key, a password or a cookie into a chat or a task. The settings page is the only place for the key.

---

## 2. Networking: Docker and the Windows host

Inside a container, `localhost` is the container itself — not your computer. ALB listens on your computer.

| Where the backend runs | Address to enter | Why |
|---|---|---|
| Docker Desktop (Windows, macOS) — default | `http://host.docker.internal:20140` | Docker Desktop provides this name for the host. |
| Docker Engine on Linux | `http://host.docker.internal:20140` | The compose file maps the name with `extra_hosts: host.docker.internal:host-gateway`. |
| Directly on the host (development) | `http://127.0.0.1:20140` | Same machine. |

Two properties of ALB matter here, both read from its source:

1. **Host guard.** ALB answers `403 This server only answers requests from this machine.` to any request whose
   `Host` header is not `127.0.0.1`, `localhost` or `::1` — on every route, whatever address it listens on. It is a
   defence against DNS rebinding from web pages. The platform therefore sends `Host: 127.0.0.1:<port>` whenever the
   address is not a loopback name. You can see and override that value in the bridge's options ("Host header");
   leave it empty to derive it. The key is still required: the header alone opens nothing.
2. **ALB listens on `127.0.0.1` by default.** Whether a container can reach a host service that listens only on
   loopback depends on how your Docker forwards `host.docker.internal`. If the "tcp" step of the diagnostics fails
   while ALB is running, your Docker does not forward to loopback. Options, safest first:
   - run the backend on the host instead of in Docker (development setups);
   - set ALB's `HOST` to the address of the Docker network interface only (not `0.0.0.0`), keep the Windows
     firewall closed for that port on public networks, and keep the key secret.

   Do **not** expose ALB to your LAN or the internet. It holds your signed-in Arena sessions.

A bridge address must be loopback, `host.docker.internal`, or a private IPv4 address. Other hosts are refused (422
`host_not_allowed`) unless you list them in `ARENA_BRIDGE_ALLOWED_HOSTS`. A URL with a user name, password, path or
query is refused (`invalid_url`).

---

## 3. Diagnostics

**Test connection** walks the path from where the backend really runs and stops at the first connection step that
fails. Nothing is sent to Arena: the steps use ALB's local routes and its `ping` probe model, which ALB answers by
itself.

| Step | What is checked | If it fails |
|---|---|---|
| `address` | The address and the `Host` header that will be sent. | — |
| `container_localhost` | Warning only: the backend is in a container and the address is `localhost`. | Use `http://host.docker.internal:20140`. |
| `dns` | The host name resolves. | On Linux add the `extra_hosts` line (the shipped compose file has it). |
| `tcp` | The port accepts a connection. | Start ALB; check the port; see "Networking" above. |
| `health` | `GET /health` answers and looks like ALB. `host_guard` → wrong `Host` header. `not_a_bridge` → another program owns the port. | Fix the header or the address. |
| `key` | The `ping` probe model answers `pong` with your key. | Enter the key ALB shows (`ARENA_AGENT_BRIDGE_KEY`). |
| `ready` | `GET /ready`: at least one account is signed in and verified. | Sign in inside ALB and wait until it is verified. |
| `models` | `GET /v1/models`: sessions that workers can use. | Create a session per account in ALB. |
| `session_registry` | `GET /api/sessions`: which account owns which session. | Optional; switch off with "Read session registry". |
| `terms` | Whether a person acknowledged the terms. Informational. | Acknowledge on the page. |

The result says separately whether the **connection** works (`dns`, `tcp`, `health`, `key`) and whether the bridge
is **ready** (an account is usable). A reachable bridge with no signed-in account is "connected, not ready".

The platform never calls ALB's `/api/status` route, because that route returns the bridge key.

---

## 4. What a worker's request looks like

`POST /v1/chat/completions` with:

| Field | Value | Reason |
|---|---|---|
| `model` | The worker's session UUID (recommended), or `active` / `agent`. | In ALB, `model` selects a conversation, not an LLM. |
| `messages` | One `user` message. | ALB passes only the last user message to a session and drops system messages. The platform folds its rules, earlier turns and the task into that one message, under the headings `### RULES FROM THE PLATFORM`, `### EARLIER IN THIS TASK`, `### YOUR TASK`. |
| `stream` | `true` by default. | ALB sends an empty chunk every 10 s; the platform uses it to tell a slow turn from a dead connection. Switch to non-streaming in the options if needed. |
| `Authorization` | `Bearer <key>` | |
| `Host` | See "Host guard". | |
| `x-codex-session-id` | Stable per worker. | ALB's own way to recognise a client conversation. |
| `x-arena-idempotency-key` | `acc-<execution id>-r<round>` — the same value on every retry of the same request. | Lets ALB replay a finished answer instead of sending the prompt to Arena a second time. |

No tools are offered: an Arena session cannot call the platform's tools. A worker that answers with a tool call is
recorded as `unexpected_tool_call` (invalid response).

**Usage is "not reported".** ALB always returns zero tokens. The platform stores usage as unknown (empty counters, no
cost) and never as `0 tokens`.

**Correlation.** ALB's `X-Arena-Bridge-Request-Id` is stored on the execution as the provider request id, next to
the platform's execution, task and trace ids.

---

## 5. Timeouts, retries and "outcome unknown"

ALB has no timeout of its own, and a turn can take minutes. The platform applies three (per bridge, in the options):

| Option | Default | Meaning |
|---|---|---|
| Connect timeout | 5 s | Opening the connection. |
| Idle timeout | 45 s | Streaming only: no bytes for this long → `stalled`. |
| Request timeout | 900 s | One whole turn. A worker's own timeout, when shorter, wins. |

Every failure is classified. The class decides whether the same request may be sent again:

| ALB answer | Code on the execution | Category | Sent again? | Reached Arena? |
|---|---|---|---|---|
| no connection | `unreachable` | bridge_unavailable | yes | no |
| stream goes silent / connection drops / total timeout | `stalled`, `connection_lost`, `timeout` | bridge_unavailable | only inside the replay window | **unknown** |
| 401 | `invalid_key` | configuration | no | no |
| 403 host guard / other 403 | `host_guard`, `forbidden` | configuration | no | no |
| 400 `empty_prompt`, `invalid_session_id`, other 400 | same / `invalid_request` | upstream / session | no | no |
| 413 | `request_too_large` | upstream | no | no |
| 409 `no_active_session`, `bound_session_dead`, `session_not_interactive` | same | session_unavailable | no | no |
| 409 `session_account_unavailable`, `credentials_unreadable` | same | account_unavailable | no | no |
| 409 `busy` | `busy` | upstream | yes | no |
| 429 `rate_limited` | `rate_limited` | rate_limit | yes, after `Retry-After` | no |
| 503 `bridge_queue_full` | `queue_full` | rate_limit | yes, after `Retry-After` | no |
| 502 or any other error | `upstream_error` | upstream | only inside the replay window | **unknown** |
| 200 with no text / not JSON / a tool call | `empty_response`, `invalid_response`, `unexpected_tool_call` | invalid_response | no | yes |

After the stream has opened, ALB reports an error as a frame inside the stream (HTTP status 200). The platform reads
that frame and classifies it the same way; it never treats a half answer as a result.

**No duplicate turns.** ALB replays a finished result for 120 s when it sees the same idempotency key. So when the
outcome of a request is unknown, the platform sends it again only within that window (option "Replay window"). After
it, the execution fails with `outcome_unknown` and is not sent to another model either: a person or the task-level
policy decides (bounded retry as a new request, or hand the task to a human). ALB's source states that a send with
an unknown outcome can still be delivered twice in rare cases; the platform cannot rule that out.

**An abandoned request keeps running.** When the platform gives up on a request (timeout, cancelled task, lost
connection), ALB is not told to stop: the turn goes on in the browser, and its answer may still appear in the Arena
conversation. The platform does not pick that answer up later. The person who takes the task over is told that the
answer may have arrived and where to look.

**One request at a time.** ALB runs one turn at a time and refuses with `bridge_queue_full` when eight are waiting.
Each platform worker process keeps at most "Max concurrent" (default 1) requests in flight per bridge and lets the
rest wait before sending. The wait is written on the execution (`waited_for_bridge_slot_ms`). For real parallelism,
run one ALB instance per account (own data directory, port and key) and add each as its own bridge.

---

## 6. Which account answers

ALB has no "use this account" field, and its answers do not say which account produced them. The platform shows
what can be known and labels it honestly:

| Shown as | When | Certainty |
|---|---|---|
| **Pinned to a session** (`deterministic_session`) | The worker's model is a session UUID. | ALB always uses the session's owner. The owner is read from ALB's session registry. |
| **Single account** (`deterministic_single_account`) | The bridge has exactly one enabled account. | By construction. |
| **Pooled** (`pooled`) | Model `active`/`agent` on a bridge with several accounts. | Not fixed: ALB picks its primary account and fails over. |
| **Unknown** (`unknown`) | The session is not in the registry, the registry is switched off, or the bridge is unreachable. | — |

Every binding carries `attested: false`: it is what ALB's configuration implies, not something each answer proves.
"Four accounts, four workers" is therefore **configured**, not verified per response.

Account e-mail addresses are masked before they are stored or shown (`o***@example.com`). Accounts are told apart
by a salted hash. The first-prompt text that ALB keeps for each session is not stored.

---

## 7. Health

The worker process reads every enabled bridge every `ARENA_BRIDGE_HEALTH_INTERVAL_SECONDS` (default 30): `/health`,
the key probe and the session registry. The page shows the last reading and its age. A bridge "works" when it is
reachable, the key is accepted and an account is ready. Changes are written to the activity log
(`workforce.bridge_health_changed`).

`GET /api/health` (the platform's own health report) includes an `arena_local_bridge` entry with counts only —
no address, account or version, because that report needs no sign-in.

**From the command line: `HEALTH-CHECK.bat` / `./health-check.sh`.** After the usual service report, the script
runs the diagnostics of section 3 **inside the API container and inside the worker container**:

```
docker compose exec -T api python -m app.connectivity.arena_bridge.probe
```

That is the only place where the answer means something: an address that opens the bridge in a browser on the host
is not the address a container has to use. The probe sends nothing to Arena and prints no key and no account
address (counts only). With no bridge configured it tries the default address — name resolution, connection,
`/health` — and says whether a bridge answers there. The bridge is optional, so an unusable bridge is reported as a
warning and does not fail the health check. Exit codes of the probe: `0` nothing configured or everything ready,
`10` a configured bridge cannot be used, `11` the probe could not run. `--json` prints the report as JSON.

---

## 8. Permissions

| Action | Permission | Roles |
|---|---|---|
| See bridges, health and bindings | `workforce.read` | all roles that can see the workforce |
| Test connection, refresh health, discover sessions | `workforce.manage` | Super Admin, Organization Admin |
| Add, change, delete a bridge; replace the key; acknowledge or revoke the terms | `workforce.bridge` (+ `credentials.manage` for the key) | **Super Admin only** |

`workforce.manage` and `workforce.bridge` are human-only: an agent or an API token acting for an agent cannot hold
them. Bridges of another organization are answered 404. Bridges cannot be created or edited through the generic
provider routes (409 `managed_at:/api/workforce/bridges`).

---

## 9. API

All under `/api/workforce/bridges`:

| Method and path | Purpose |
|---|---|
| `GET ""` | List, with `default_url`, `in_container`, the terms URL. |
| `POST ""` | Create (`name`, `base_url`, `api_key`, optional `settings`). |
| `GET /{id}` · `PATCH /{id}` · `DELETE /{id}` | Read, change, delete (409 `bridge_in_use` while a worker uses it). |
| `PUT /{id}/key` | Replace the key. The old one is deleted. |
| `POST /{id}/terms` · `DELETE /{id}/terms` | Acknowledge with the exact statement / revoke. |
| `POST /{id}/test` | Diagnostics (section 3). |
| `POST /{id}/health` | One reading now. |
| `POST /{id}/discover` | Read the sessions and register them as selectable models. |

No response contains the key.

---

## 10. The fake bridge (tests and demos only)

`backend/devtools/fake_arena_bridge.py` imitates ALB v1.1.0 from its source: routes, host guard, key check, queue,
rate limit, idempotent replay, stream format, error bodies. It is used by the automated tests over real HTTP.

```
cd backend
uv run python -m devtools.fake_arena_bridge --port 20140 --key local-dev-key
```

It always says what it is: `service: "fake-arena-bridge"` in `/health`, the header `X-Fake-Arena-Bridge: 1`, and
every answer starts with `[FAKE ARENA BRIDGE — not Arena]`. A bridge entry that talks to it is marked **mock**, and
so is every execution, usage record and report that came through it. It can never pass for an Arena result.

---

## 11. Checking a real bridge on your machine

Nobody ran the platform against a real ALB during development. Two ways to do it, from least to most involved.

**A. By hand, in the web app** (sends nothing until step 5):

1. `HEALTH-CHECK.bat`. With no bridge configured, the probe should show `OK dns`, `OK tcp`, `OK health` for the
   default address while ALB is running.
2. Add the bridge on the bridge page. Press **Test connection**: every step up to `terms` should be green and the
   account count should be what you expect.
3. Acknowledge the terms. Press **Discover sessions**.
4. Give W1 one session (Manager & workers → *Configure*). Its card should read *One session of one account*.
5. Submit a prompt with one small research task. The task should complete as *Automatic*, the result should not be
   labelled mock, and usage should read *not reported*.
6. Repeat step 4 for W2–W4 with sessions of the other accounts. The Overview should count four different accounts.

**B. The automated tier: `REAL-TESTS.bat`, choice 2** (`pytest -m real_arena`). It connects, checks that four
accounts are ready, binds four workers to four sessions of four different accounts, runs one objective with four
tasks, and repeats one request to see that the bridge replays it instead of sending it twice. It sends five short
prompts to Arena and asks you to type `YES` first. It uses a separate test database. Details and settings:
`backend/tests/test_real_arena.py`. On Linux or macOS:

```
REAL_TIER_REQUIRED=real_arena REAL_ARENA_ACCEPT_TERMS=yes REAL_ARENA_BRIDGE_KEY=<key> \
  docker compose run --rm -T realtests -m real_arena tests/test_real_arena.py -v -s
```

The same steps run in the regular test suite against the fake bridge (`tests/test_real_tiers_selftest.py`), so the
steps themselves are known to work. What they find on a real bridge is not known.

---

## What is not verified

Built from ALB's source and tested only against the fake bridge. These need your Windows machine, a running ALB and
your own Arena accounts:

- a real request through ALB to Arena, streaming and non-streaming;
- `host.docker.internal` reaching an ALB that listens on `127.0.0.1` under your Docker Desktop;
- the `Host` header override against the real host guard;
- the real error bodies and status codes for each failure;
- idempotent replay with the real cache;
- four sessions owned by four different accounts, and one ALB instance per account.

`HEALTH-CHECK` reports each step from inside the API and worker containers, so you can check the networking part
without sending anything to Arena. Section 11 describes how to check the rest. Neither `HEALTH-CHECK.bat` nor
`REAL-TESTS.bat` has been run on Windows.
