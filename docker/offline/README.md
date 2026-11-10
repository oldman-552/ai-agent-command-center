# Offline verification images

**Not used on your machine.** `START.bat` uses the official images (postgres, rabbitmq,
python, node) from Docker Hub.

These files exist only because the cloud development environment blocks every container
registry. They rebuild equivalent services on a locally-imported Ubuntu 24.04 base
(`acc/ubuntu-base:noble`, created with `debootstrap` from archive.ubuntu.com) so the full
compose stack (healthchecks, migrations, volumes, restarts) can be tested end-to-end. `base.Dockerfile` adds the
`noble-updates`/`noble-security` suites to that base and applies every update (debootstrap configures neither).
The frontend image copies the host build, so build it with `API_INTERNAL_URL=http://api:8000 npm run build` first.

```
docker compose -f docker-compose.yml -f docker/offline/compose.offline.yml up -d --build
```
