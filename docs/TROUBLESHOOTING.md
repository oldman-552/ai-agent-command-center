# Troubleshooting

Start with the health report: `HEALTH-CHECK.bat` / `./health-check.sh`, or open `http://localhost:8000/api/health`.
Script logs are in `logs/`; service logs: `docker compose logs <service> --tail 200`.

| Symptom | Cause and fix |
|---|---|
| "Docker Desktop is not installed / not running" | Install/start Docker Desktop and wait for *Engine running*. |
| A port is already in use | Change `FRONTEND_PORT` / `API_PORT` / `RABBITMQ_UI_PORT` in `.env`, then start again. |
| API never becomes ready | `docker compose logs migrate api`. A failed migration stops the API; an empty `ADMIN_PASSWORD`, `JWT_SECRET` or `APP_ENCRYPTION_KEY` makes startup refuse to run. |
| Health shows `degraded`, workers `none` | The worker is not running: `docker compose logs worker`. Without it, objectives are not planned and executions queue. |
| Health shows `redis: error` | Live updates stop; pages fall back to periodic refresh. Restart `redis`. |
| Header shows *Offline — refreshing periodically* | The WebSocket `/api/ws` is blocked (reverse proxy without WebSocket upgrade) or Redis is down. |
| "provider … has no API key" / 401 from a provider | Add a key under **Providers & Models**. A Claude.ai / ChatGPT / Gemini subscription is not an API key. |
| Executions fail with `agent_circuit_open` / provider skipped | Repeated failures opened a circuit; see **Agent Runtime**. It closes after the cool-down when a trial succeeds. |
| New work refused with `budget_exceeded` | A budget is exhausted (**Analytics → Budgets**); approve the generated *Additional spending* request or raise the limit. |
| Shell tools return `sandbox unavailable` | The worker cannot reach a Docker daemon, or `acc/sandbox:python` is missing (`docker build -t acc/sandbox:python docker/sandbox`). |
| CLI engine: "… is not installed in acc/cli:latest" | Build `docker/cli/Dockerfile`; set the agent's CLI credential. |
| Merge approved but not executed | The branch moved after approval or conflicts appeared — the approval shows the reason; request a new review. |
| Browser Bridge: `bridge_disabled` / `terms_not_acknowledged` | Enabled and unlocked only by the Super Admin on the Browser Bridge page. |
| Browser Bridge: `interface_changed` | The provider changed its page; the adapter's selectors no longer match. Prefer the official API. |
| Browser Bridge: `login_required` / `captcha_detected` | A person must sign in / solve it in the bridge window (`BROWSER_HEADLESS=false` on the host), then *check again*. |
| Lost `APP_ENCRYPTION_KEY` | Stored credentials cannot be decrypted; re-enter provider keys and tokens after setting a new key. |
| Need a clean slate (development only) | `RESET.bat` deletes all volumes after you type `RESET`. Arena Local Bridge and its accounts are not touched. |

## AI Workforce and Arena Local Bridge

Start with `HEALTH-CHECK.bat` / `./health-check.sh`: it probes the bridge from inside the API and worker containers
and names the failing step. On the bridge page, **Test connection** shows the same steps.

| Symptom | Cause and fix |
|---|---|
| The prompt form is disabled: "the Manager has no model" | Manager & workers → *Configure* on the Manager card → choose a provider and a model. |
| `manager_cannot_use_a_bridge` | The Manager needs a real model API. An Arena session cannot be the Manager. |
| An objective stays in *planning* | The worker process plans it. Health shows workers `none` → `docker compose logs worker`. If the Manager's model cannot be reached, the objective fails after the fifth try and shows the reason. |
| A task waits: "no worker can take it" | The card names the reason (not configured, provider disabled, terms not acknowledged, bridge or account unavailable, worker disabled). See [AI_WORKFORCE.md](AI_WORKFORCE.md#9-when-something-does-not-move). |
| A task of an Automatic worker was handed to a person | The attempt failed in a way that must not be retried blindly, or the attempts were used up. The card shows the failure. If it says the answer may have arrived, look in the Arena conversation before redoing the work. |
| A result waits for "a person's review" | The Manager escalated it, the revision limit was reached, or the text looks like an instruction or a credential. Open the task and decide. |
| A critical task stays in *Reviewed — awaiting approval* | By design. Approve it in the **Approval Center**. |
| Approval Center: "You cannot decide on a request you made yourself" | You submitted that result. Another person with the approval right must decide. |
| The class *Verified by the platform* is always 0 | Nothing produces that class yet (development tasks in a worktree are not implemented). |
| Probe step `dns` fails | `host.docker.internal` does not resolve. Docker Desktop provides it; on Linux the compose file maps it. Check that you use the shipped compose file. |
| Probe step `tcp` fails | The bridge is not running, uses another port, or your Docker does not forward to a program that listens only on `127.0.0.1`. See [ARENA_LOCAL_BRIDGE.md](ARENA_LOCAL_BRIDGE.md#2-networking-docker-and-the-windows-host). |
| Probe step `health` fails with `host_guard` | The bridge only answers a loopback `Host` header. Leave the "Host header" option empty so the platform derives it. |
| Probe step `health` fails with `not_a_bridge` | Another program uses that port. |
| Probe step `key` fails | Enter the key the bridge shows (`ARENA_AGENT_BRIDGE_KEY`). |
| Probe step `ready` fails | No Arena account is signed in and verified in the bridge. Sign in there. |
| "Connected, not ready" | The connection works, but the bridge has no usable account. |
| The address `http://127.0.0.1:20140` works in the browser but not in the platform | In a container, `127.0.0.1` is the container. Use `http://host.docker.internal:20140`. |
| `terms_not_acknowledged` | The Super Admin acknowledges the terms on the bridge page. Changing the bridge's address revokes the acknowledgement. |
| A worker shows *Account unknown* | The session is not in the bridge's registry, or the bridge was unreachable at the last reading. Press *Discover sessions*, then check the worker's session. |
| Usage shows "not reported" | The bridge reports no token counts. The platform does not invent them. |
| `REAL-TESTS.bat` ends with "did not reach the test" | A setting was empty. Run it again and enter the value. |
| `REAL-TESTS.bat`: "this address answers as the FAKE bridge" | The address points at the development fake, not at Arena Local Bridge. |
