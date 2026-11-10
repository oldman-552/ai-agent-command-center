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
| Need a clean slate (development only) | `RESET-DEV.bat` deletes all volumes after you type `RESET`. |
