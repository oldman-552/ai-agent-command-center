# Sandbox image for agent shell tools (SEC-04). Runs as uid 10001 with a read-only root filesystem;
# the only writable places are /workspace (the task's project workspace) and a small /tmp tmpfs.
#   docker build -t acc/sandbox:python -f docker/sandbox/Dockerfile .
# The offline/dev build uses a locally bootstrapped Ubuntu base: --build-arg BASE=acc/ubuntu-base:noble
ARG BASE=ubuntu:24.04
FROM ${BASE}
ARG https_proxy
ARG http_proxy
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-pytest ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/python3 /usr/local/bin/python \
 && ln -sf /usr/bin/pytest-3 /usr/local/bin/pytest 2>/dev/null || true \
 && useradd --uid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin sandbox
USER 10001:10001
WORKDIR /workspace
