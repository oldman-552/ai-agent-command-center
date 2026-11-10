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
| `a2a` | A2A engine | A2A v1.0 JSON-RPC | polling | agent card | remote agents, see A2A_INTEGRATION.md |
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
masked in output. Only flags confirmed in documentation are used:

| CLI | Command the platform runs | Key variable | Flags verified (source, 2026-11-10) | Caveats |
|---|---|---|---|---|
| Claude Code | `claude --bare -p <prompt> --output-format json --permission-mode acceptEdits [--model M]` | `ANTHROPIC_API_KEY` | `--bare`, `-p`, `--output-format json`, `--permission-mode acceptEdits`, `ANTHROPIC_API_KEY` in bare mode — [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless) | `--model` from the CLI reference (not re-fetched). Result JSON fields used: `result`, `is_error`, `usage`; `total_cost_usd` is the CLI's own estimate and is **not** recorded as cost |
| OpenAI Codex CLI | `codex exec --sandbox workspace-write --cd /workspace [--model M] <prompt>` | `OPENAI_API_KEY` | `exec`, `--sandbox workspace-write`, `--cd`, `--model` — from a mirror of the official docs ([mintlify.com/openai/codex](https://www.mintlify.com/openai/codex/concepts/non-interactive-mode)); the official page redirected and could not be fetched | final message read from stdout; `--yolo` / `--full-auto` deliberately not used |
| Gemini CLI | `gemini -p <prompt> --output-format json [--model M]` | `GEMINI_API_KEY` | `-p`, `--output-format json` (`response`, `stats`, `error`) — [geminicli.com/docs/cli/headless](https://www.geminicli.com/docs/cli/headless) | `--model` and `GEMINI_API_KEY` from the CLI reference (not fetched); approvals left at the default (no `--yolo`) |
| OpenCode | `opencode run [--model provider/model] <prompt>` | per provider — set `context_policy.cli_key_env` | `run`, `--model provider/model` — [opencode.ai/docs/cli](https://opencode.ai/docs/cli) | default text output (JSON event schema undocumented) |

Tested with a fake runner (argv, environment, parsing, failures) and a real Docker sandbox for the secret-passing path.
**None of the four CLIs has been run for real here** (not installed in the development container, and no API keys).

## Not supported — by design or not yet

| Integration | Status | Why / alternative |
|---|---|---|
| Consumer subscriptions as API access (Claude.ai, ChatGPT, Gemini apps) | **Not supported** | Not an API; terms. Use API keys. |
| Automating consumer chat websites | **Experimental only** (Browser Bridge) | Disabled by default, per-provider terms acknowledgement, unverified selectors; prefer APIs/CLIs. |
| Amazon Bedrock, Google Vertex AI, Azure OpenAI native auth | Not implemented | Use an OpenAI-compatible endpoint where the vendor offers one, or add an adapter. |
| LiteLLM proxy | Not used | Own adapters cover the providers in scope (ADR-004); an OpenAI-compatible LiteLLM endpoint works as `openai_compatible`. |
| Embedding models for memory | Not used | Local hashed embedding (ADR-024); swappable later. |
| Image / audio generation endpoints | Not implemented | Text chat with tools only. |
| A2A streaming, push notifications, extended card | Not implemented | Answered with the spec's errors; polling is used. |
| CLI interactive modes, `--yolo`/`--dangerously-skip-permissions` | Not used | Non-interactive only; no approval bypass. |
| Cursor, Windsurf, Aider and other IDE agents | Not integrated | No documented, stable non-interactive contract verified. |

## Verification status

All adapters are tested against the providers' HTTP APIs simulated at the transport layer (`httpx.MockTransport`).
**No live call with a real key has been made yet (GW-10)** — add your key under Providers & Models and use
*Test connection*.
