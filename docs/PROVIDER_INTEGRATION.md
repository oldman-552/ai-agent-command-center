# Provider integration

How the platform reaches AI models, what is supported, and — just as important — what is **not** (EE-04).

## Principles

- Official APIs with API keys created in each provider's console. **Consumer subscriptions (Claude Pro/Max,
  ChatGPT Plus/Pro, Gemini/Google One) do not include API access** and are never used.
- Keys are encrypted at rest, write-only in the API, decrypted only for the call, never put into prompts or logs.
- Prices are only what you configure per model; a model without a price records usage but no cost — nothing is
  estimated or invented. Mock usage never counts as cost.
- Every call records usage (input/output/cached tokens, duration, retries) and, when priced, a cost record.

## Model gateway (`api` engine)

| Kind | Adapter | Transport | Streaming | Model discovery | Notes |
|---|---|---|---|---|---|
| `anthropic` | `AnthropicAdapter` | official `anthropic` SDK | yes | yes (`/v1/models`) | cached-token pricing supported |
| `openai` | `OpenAIAdapter` | official `openai` SDK | yes | yes | |
| `openai_compatible` | `OpenAICompatibleAdapter` | `openai` SDK with `base_url` | yes | when the server supports it | local servers (Ollama, LM Studio, vLLM…) and other vendors with an OpenAI-compatible API; key optional |
| `google` | `GoogleAdapter` | Gemini REST API over httpx2 (ADR-013) | yes | yes | |
| `a2a` | A2A engine | A2A v1.0 JSON-RPC | SSE when the card offers it, else polling | agent card | remote agents, see A2A_INTEGRATION.md |
| `mock` | `MockAdapter` | none | yes | fixed list | output prefixed `[MOCK — not real AI]`; tests and demos only |

Errors are classified: 429, 5xx and network errors → `ProviderUnavailable` (retried, counts for the provider's
circuit); every other HTTP error (400/401/403/404…) → `ProviderError` (configuration; not retried). An agent has a primary and an optional
fallback provider/model; a failed primary is skipped to the fallback before any output is streamed (audited
`gateway.fallback`). With `context_policy.routing = "auto"` the gateway ranks every enabled model by availability,
capabilities, context window, configured cost, observed latency and reliability (ADR-027); `POST /api/routing/preview`
explains a decision. LiteLLM is not used (ADR-004).

## Coding CLIs (`cli` engine, EE-02)

Run non-interactively in a fresh sandbox container (image `acc/cli:latest` from `docker/cli/Dockerfile`, which you
build; network enabled because the CLI must reach its API). The key is passed as an environment variable by name and
masked in output. Flags were checked against each CLI's own `--help` (versions below, 2026-11-10) and its docs:

| CLI (version checked) | Command the platform runs | Key variable | Notes |
|---|---|---|---|
| Claude Code (2.1.322) | `claude --bare -p <prompt> --output-format json --permission-mode acceptEdits [--model M]` | `ANTHROPIC_API_KEY` | [headless](https://code.claude.com/docs/en/headless), [CLI reference](https://code.claude.com/docs/en/cli-reference). `--bare` skips project hooks, CLAUDE.md, plugins and MCP servers. Result JSON fields used: `result`, `is_error`, `usage`; `total_cost_usd` is the CLI's own estimate and is **not** recorded as cost |
| OpenAI Codex CLI (0.167.0) | `codex exec --sandbox workspace-write --cd /workspace --skip-git-repo-check [--model M] <prompt>` | `OPENAI_API_KEY` and `CODEX_API_KEY` (the binary reads both) | final message read from stdout; `--yolo` / `--full-auto` deliberately not used; refuses a workspace with `.codex/` |
| Gemini CLI (0.67.0) | `gemini -p <prompt> --output-format json --skip-trust [--model M]` | `GEMINI_API_KEY` | [headless](https://www.geminicli.com/docs/cli/headless) (`response`, `stats`, `error`). Headless runs exit with code 55 in an untrusted folder, so the workspace is trusted for the session (`--skip-trust`) — and the engine refuses workspaces containing `.gemini/` configuration, which trust would load. Approvals stay at the default (no `--yolo`) |
| OpenCode (1.19.5) | `opencode run [--model provider/model] <prompt>` | per provider — set `context_policy.cli_key_env` | [CLI docs](https://opencode.ai/docs/cli); default text output (JSON event schema undocumented); refuses a workspace with `opencode.json(c)` or `.opencode/` |

**Setting one up.** In the agent form choose *Execution engine → Command-line tool*, pick the tool and select a
provider with an API key of the matching kind (Anthropic for Claude Code, OpenAI for Codex, Google for Gemini; any
for OpenCode, which also needs the name of the key variable). The CLI uses that provider's key and the agent's model;
the form warns when the provider kind does not fit. (`context_policy.cli_credential_id` can still point at another
stored credential.)

**Project configuration.** A repository can carry configuration for these CLIs that widens tool approvals or adds
commands and MCP servers — and the API key is in the container's environment. The engine therefore refuses to start
a CLI when its project configuration exists in the workspace, unless the agent's `context_policy.cli_allow_project_config`
is `true` (a deliberate choice for repositories you control).

**Verification.** Unit tests with a fake runner (argv, environment, parsing, failures, the project-configuration
guard) and a real Docker sandbox for the secret-passing path. In addition, all four CLIs were installed in an offline
equivalent of the CLI image (`docker/offline/cli.Dockerfile`) and run inside the real sandbox (read-only root
filesystem, uid 10001, `HOME=/tmp`) with the engine's exact argv and a dummy key: each starts, accepts its flags and
key, and stops only at the network (Claude Code returned its JSON result with `is_error`; Codex and Gemini retried the
blocked provider API; OpenCode failed on the development proxy's certificate); the key never appeared in output.
That run found and fixed the Gemini trust problem above. **No CLI has completed a real task here** (provider APIs are
blocked and there are no keys).

## Not supported — by design or not yet

| Integration | Status | Why / alternative |
|---|---|---|
| Consumer subscriptions as API access (Claude.ai, ChatGPT, Gemini apps) | **Not supported** | Not an API; terms. Use API keys. |
| Automating consumer chat websites | **Experimental only** (Browser Bridge) | Disabled by default, per-provider terms acknowledgement, unverified selectors; prefer APIs/CLIs. |
| Amazon Bedrock, Google Vertex AI, Azure OpenAI native auth | Not implemented | Use an OpenAI-compatible endpoint where the vendor offers one, or add an adapter. |
| LiteLLM proxy | Not used | Own adapters cover the providers in scope (ADR-004); an OpenAI-compatible LiteLLM endpoint works as `openai_compatible`. |
| Embedding models for memory | Not used | Local hashed embedding (ADR-024); swappable later. |
| Image / audio generation endpoints | Not implemented | Text chat with tools only. |
| A2A input-required mid-task, non-text parts | Not implemented | No engine asks for input; text parts only. (Streaming, push, extended card and multi-turn are served.) |
| CLI interactive modes, `--yolo`/`--dangerously-skip-permissions` | Not used | Non-interactive only; no approval bypass. |
| Cursor, Windsurf, Aider and other IDE agents | Not integrated | No documented, stable non-interactive contract verified. |

## Verification status

All adapters are tested against the providers' HTTP APIs simulated at the transport layer (`httpx.MockTransport`).
**No live call with a real key has been made yet (GW-10)** — add your key under Providers & Models and use
*Test connection*.
