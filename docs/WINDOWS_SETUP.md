# Windows setup (Windows 10 / 11)

## 1. Install Docker Desktop

Download from <https://www.docker.com/products/docker-desktop/>, install with the default WSL 2 backend, restart if
asked, and start Docker Desktop. Wait until it shows **Engine running**. The launchers never install software for you.

## 2. Get the project

Clone it with Git (`git clone https://github.com/oldman-552/ai-agent-command-center.git`) or download the ZIP and
extract it. Paths with spaces or Arabic characters are fine.

## 3. Start

Double-click **`START.bat`**. On the first run it:

1. checks that Docker Desktop is installed and running (with a clear message and the download link if not);
2. creates `.env` from `.env.example` and generates `JWT_SECRET`, `APP_ENCRYPTION_KEY`, `POSTGRES_PASSWORD` and
   `RABBITMQ_PASSWORD`; asks you for the **Super Admin password** (at least 12 characters, hidden while typing);
3. builds the images (several minutes the first time) and starts all services;
4. waits for the database, RabbitMQ, the API (migrations run automatically) and the web app;
5. opens <http://localhost:3000> in your browser.

Sign in with `admin` and your password. Every run writes a log to the `logs` folder.

| File | Use |
|---|---|
| `START.bat` | Start (safe to run again) |
| `STOP.bat` | Stop; keeps all data |
| `UPDATE.bat` | Update the code (`git pull`) and restart |
| `RESET-DEV.bat` | Delete **all** data after typing `RESET` (keeps `.env`) |
| `INSTALL.ps1` | First-time setup without starting (right-click → Run with PowerShell) |
| `HEALTH-CHECK.ps1` / `HEALTH-CHECK.bat` | Show the health of every service |

The `.bat` files call `scripts\windows\acc.ps1` with `-ExecutionPolicy Bypass` for that process only; your system
policy is not changed.

## 4. Ports

Defaults: web `3000`, API `8000`, RabbitMQ management `15672` — all bound to `127.0.0.1` only. Change
`FRONTEND_PORT`, `API_PORT`, `RABBITMQ_UI_PORT` in `.env` if they are taken.

## 5. Agent sandbox on Windows

Shell tools run in Linux containers through Docker Desktop. `INSTALL.ps1` builds the sandbox image
(`acc/sandbox:python`). Giving the worker access to Docker is a deliberate choice — see
[DEPLOYMENT.md](DEPLOYMENT.md#sandboxes-and-docker-access).

## Verification status

The Windows scripts were checked statically (CRLF endings, `%~dp0` quoting, balanced PowerShell syntax) in a Linux
environment; **they have not yet been run on a real Windows machine**. If something fails, the log in `logs\` and
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) will help; please report it.
