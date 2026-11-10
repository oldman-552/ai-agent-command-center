# Sandbox image for the `cli` execution engine (EE-02, ADR-029). Built by the owner — NOT built or verified in the
# development container (no npm registry access for image builds there). Pin versions you have reviewed.
#   docker build -t acc/cli:latest docker/cli
FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates python3 \
 && rm -rf /var/lib/apt/lists/*
# Official packages (npm): Claude Code, OpenAI Codex CLI, Gemini CLI, OpenCode
ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest
ARG GEMINI_CLI_VERSION=latest
ARG OPENCODE_VERSION=latest
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
      "@google/gemini-cli@${GEMINI_CLI_VERSION}" "opencode-ai@${OPENCODE_VERSION}" \
 && npm cache clean --force
# The sandbox runs every container as uid 10001 with a read-only root filesystem and HOME=/tmp (tmpfs).
RUN useradd --uid 10001 --create-home sandbox
USER 10001
WORKDIR /workspace
