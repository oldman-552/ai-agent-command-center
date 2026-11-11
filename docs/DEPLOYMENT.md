# Deployment

The supported deployment is Docker Compose on one host (Windows with Docker Desktop, Linux, macOS, a cloud VM).

## Files

| File | Purpose |
|---|---|
| `docker-compose.yml` | All services with health checks and named volumes: `postgres`, `rabbitmq`, `redis`, `migrate` (one-shot), `api`, `worker`, `browser-bridge` (experimental), `frontend` |
| `docker-compose.prod.yml` | Production overrides: `restart: always`, resource limits, `COOKIE_SECURE=true`, JSON logs, OTLP tracing, no RabbitMQ UI on the host, API/web bound to `127.0.0.1` for a reverse proxy, Browser Bridge only with `--profile browser-bridge` |
| `.env.example` | Environment template; the launchers create `.env` from it and generate every secret except the Super Admin password |
| `backend/Dockerfile`, `backend/Dockerfile.bridge`, `frontend/Dockerfile` | Service images |
| `docker/sandbox/Dockerfile` | Sandbox image for agent shell tools (`acc/sandbox:python`) |
| `docker/cli/Dockerfile` | Optional image with the coding CLIs for the `cli` engine (`acc/cli:latest`) |

## Steps (production)

1. Install Docker Engine + Compose v2 on the host. Clone the repository.
2. `./install.sh` (creates `.env` with generated secrets, builds images, builds the sandbox image).
3. Put a TLS reverse proxy in front of `127.0.0.1:3000` (the web app proxies `/api` and the WebSocket to the API).
   WebSocket upgrade must be allowed for `/api/ws`.
4. `ACC_PROFILE=production ./start.sh` (equivalent: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`).
5. Sign in as the Super Admin, change the password, add providers, users and agents.

## Persistence and backups

Named volumes: `pgdata` (database — everything important), `rabbitdata` (broker state), `workspaces` (project
repositories and task worktrees), `workforce-attachments` (files people attach to Human-Assisted results; the database
holds their names and hashes, this volume holds the bytes), `browser-profiles` (bridge profiles). `STOP`/`stop.sh`
never remove volumes; only `RESET-DEV.bat` (after typing `RESET`) does.

Back up the database (`docker compose exec postgres pg_dump -U acc acc > backup.sql`), the `workspaces` and
`workforce-attachments` volumes, and `.env` — **`APP_ENCRYPTION_KEY` is required to read stored credentials**.

## Sandboxes and Docker access

Agent shell tools and the CLI engine start containers. The worker therefore needs a Docker daemon. The compose file
does not mount the host's Docker socket by default because that is root-equivalent on the host; options, in order of
preference: a dedicated Docker host (`DOCKER_HOST=tcp://…` with TLS), rootless Docker, or mounting the socket on a
host dedicated to this platform. Without a daemon the shell tools report `sandbox unavailable`; everything else works.

## Observability

`/api/health` (used by the compose health check) reports database, RabbitMQ, Redis, worker heartbeats and the
Browser Bridge. Logs are JSON on stdout (`docker compose logs`). Set `OTEL_EXPORTER_OTLP_ENDPOINT` to send traces to
any OTLP/HTTP collector.

## Scaling out

The API is stateless apart from PostgreSQL and Redis: run several `api` replicas behind a reverse proxy that passes
WebSocket upgrades for `/api/ws` (no sticky sessions needed). Sessions are signed tokens, sign-in throttling and live
updates go through Redis, background work through RabbitMQ. Workers scale with `docker compose up --scale worker=N`;
the supervisor and automations elect a leader.

`deploy/nginx/acc.conf` is a ready reverse proxy: TLS, http→https redirect, `/api/ws` with WebSocket upgrade,
`/api/` (buffering off for A2A/MCP streams) balanced with `least_conn` across the API replicas, everything else to
the web app. List one `server` line per API replica (on one host, give each replica its own `127.0.0.1` port; the
production compose file binds a single API to `127.0.0.1:${API_PORT}`). It works with nginx 1.24 (Ubuntu 24.04)
and later.

Verified (`backend/tests/test_replicas.py`): two API processes on the same PostgreSQL and Redis — sessions,
sign-in throttling and live updates cross them — and the same through a real nginx started from that file (requests
of one session answered by both replicas, a WebSocket upgrade carrying changes made through either). The TLS part is
checked with `nginx -t` and a throwaway certificate.

## Upgrades

`./update.sh` / `UPDATE.bat`: fast-forward pull, rebuild with fresh base images, restart; the `migrate` service
applies Alembic migrations before the API and worker start.

## Verified here vs. on your machine

The compose stack (healthchecks, migrations, volumes, restarts) was exercised in the development container with
locally rebuilt equivalents of the official images (`docker/offline/`, because that environment blocks container
registries). The official images, the Playwright bridge image, the CLI image and Trivy scans have **not** been built
or run there — run `./install.sh` and `scripts/security-scan.sh --strict` on your machine.
