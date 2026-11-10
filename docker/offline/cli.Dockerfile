# Offline equivalent of docker/cli/Dockerfile (registries blocked in the development environment). The build context
# is a folder with `node` (the same binary as docker/offline/node) and a `node_modules` holding the four CLIs, made
# with `npm install @anthropic-ai/claude-code @openai/codex @google/gemini-cli opencode-ai` on the host.
#   docker build --network host -t acc/cli:latest -f docker/offline/cli.Dockerfile <that folder>
FROM acc/ubuntu-base:noble
ARG https_proxy
ARG http_proxy
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*
COPY node /usr/local/bin/node
COPY node_modules /opt/clis/node_modules
RUN for b in claude codex gemini opencode; do ln -s "/opt/clis/node_modules/.bin/$b" "/usr/local/bin/$b"; done \
 && useradd --uid 10001 --create-home sandbox
USER 10001
WORKDIR /workspace
