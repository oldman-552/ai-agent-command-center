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
| `RESET.bat` | Delete **all** data after typing `RESET` (keeps `.env`). `RESET-DEV.bat` does the same. |
| `INSTALL.bat` | First-time setup without starting (`INSTALL.ps1` is the same for PowerShell) |
| `HEALTH-CHECK.bat` | Show the health of every service, and whether the containers can reach Arena Local Bridge (`HEALTH-CHECK.ps1` is the same) |
| `REAL-TESTS.bat` | The two checks that leave your computer (section 6). Asks before anything is sent. |

The `.bat` files call `scripts\windows\acc.ps1` with `-ExecutionPolicy Bypass` for that process only; your system
policy is not changed.

## 4. Ports

Defaults: web `3000`, API `8000`, RabbitMQ management `15672` — all bound to `127.0.0.1` only. Change
`FRONTEND_PORT`, `API_PORT`, `RABBITMQ_UI_PORT` in `.env` if they are taken.

## 5. Agent sandbox on Windows

Shell tools run in Linux containers through Docker Desktop. `INSTALL.ps1` builds the sandbox image
(`acc/sandbox:python`). Giving the worker access to Docker is a deliberate choice — see
[DEPLOYMENT.md](DEPLOYMENT.md#sandboxes-and-docker-access).

## 6. AI Workforce and Arena Local Bridge

The AI Workforce works after `START.bat` with no extra setup: sign in, open **AI Workforce**, choose the Manager's
model. The four workers start as Human-Assisted. Guide: [AI_WORKFORCE.md](AI_WORKFORCE.md).

**Arena Local Bridge** is optional and is a separate program that you install and run on Windows yourself
(`ArenaLocalBridge.exe` or its `start-gui.bat`). The platform does not install, start or update it.

- The platform runs in Docker. From a container, your computer is `host.docker.internal`, not `localhost`. So the
  address to enter on the bridge page is `http://host.docker.internal:20140`.
- **`HEALTH-CHECK.bat` tells you whether that works.** It runs a probe inside the API container and inside the
  worker container and prints each step: name resolution, connection, the bridge's `/health`, the key, ready
  accounts, sessions, terms. It sends nothing to Arena and prints no key and no account address. With no bridge
  configured it tries the default address and says whether a bridge answers there.
- An unusable bridge is a **warning**, not a failed health check: the platform works without it.

**`REAL-TESTS.bat`** runs the two checks nobody could run during development. Each asks before it does anything.

| Choice | What it checks | What it uses |
|---|---|---|
| 1 | The Manager analyses, plans, reviews and summarises on a real model. | Your API key and a model id. About 5 to 8 requests that you pay for. No Arena. |
| 2 | Four workers, each on its own session of a different Arena account, answer through the bridge; a repeated request is not sent twice. | A running bridge with four signed-in accounts and one conversation per account. Sends 5 short prompts to Arena. You type `YES` to acknowledge the terms first. |

Both run in a throwaway container on a **separate database** (`acc_owner_test`, created on first use inside the same
PostgreSQL). Your data, the broker and Redis are not touched. Keys are typed hidden, kept in memory for the run and
never written to a file or the log. The log in `logs\` is your evidence.

`RESET.bat` deletes the platform's data, including the bridge settings and stored keys. It does not touch Arena
Local Bridge or its accounts.

## Verification status

**None of the Windows scripts has been run on a real Windows machine.** No Windows and no Docker were available
during development. What was done instead, in a Linux environment:

- static checks of every file: CRLF line endings, ASCII-only batch files, UTF-8 with byte-order mark for the
  PowerShell script, balanced brackets and quotes, each `.bat` calls an action that exists
  (`backend/tests/test_launch_scripts.py`);
- `RESET` asks for the typed word before anything is deleted, and no other action removes volumes (same file);
- the Linux `health-check.sh`, which has the same steps, was executed against stand-ins for `docker` and `curl`;
- the bridge probe itself was executed against a fake bridge (`backend/tests/test_arena_bridge_probe.py`);
- the command that `REAL-TESTS.bat` runs in the container was executed outside Docker, in a folder laid out like
  the image, with the mock Manager and the fake bridge.

That is not a substitute for a real run. If something fails, the log in `logs\` and
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) will help; please report it.
