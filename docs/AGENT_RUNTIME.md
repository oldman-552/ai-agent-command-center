# Agent runtime

## Agents

An agent is a configuration (`agents` table, versioned in `agent_config_versions`): role, system instructions,
provider/model (+ fallback) or `context_policy.routing = "auto"`, execution engine, tool permissions, MCP servers,
skills, security profile, token/cost budget, timeout, retry policy, concurrency limit, priority, supervisor agent.
Eighteen built-in templates are created at bootstrap. Lifecycle states: `CREATED → INITIALIZING → IDLE → ASSIGNED →
RUNNING → REVIEWING → COMPLETED`, plus `WAITING_FOR_INPUT`, `WAITING_FOR_APPROVAL`, `BLOCKED`, `FAILED`, `CANCELLED`,
`DISABLED`; every transition is recorded with its actor and reason (`agent_state_transitions`).

## Executions

An execution is one run of an agent (`executions`, events in `execution_events`, checkpoints in `checkpoints`).

```
CREATED → INITIALIZING → READY → RUNNING ⇄ WAITING_FOR_TOOL | WAITING_FOR_APPROVAL | WAITING_FOR_INPUT
RUNNING → CHECKPOINTED → RUNNING …        RUNNING → COMPLETED | FAILED | CANCELLED | TERMINATED
FAILED | TERMINATED → RECOVERING → RUNNING     (resume from the latest checkpoint)
```

- Creation checks: agent enabled, engine registered, concurrency limit, agent circuit closed, **budgets** in scope;
  an `idempotency_key` returns the same execution instead of a second run.
- Dispatch: through RabbitMQ (`EXECUTION_DISPATCH=bus`, default in deployments) or inline (tests).
- Every engine event is stored with a sequence number: `state`, `output_delta`, `tool_call`, `tool_result`, `log`,
  `usage`, `error`, `checkpoint`. The UI shows them as the execution timeline; state changes are pushed live.
- Tools: the engine asks; the runtime checks the agent's tool permissions and runs the registered tool
  (`files.write`, `repo.read`, `git.branch`, `git.commit`, `pr.create`, `shell.test`, `shell.build`, `memory.write`,
  `mcp:<server>:<tool>`, …). Denials are policy errors, audited, and never retried.
- Failures are categorised (`transient`, `timeout`, `provider`, `policy`, `error`); only transient ones are retried
  automatically (5 s / 30 s / 300 s tiers, then the DLQ). The recovery report records what was tried.
- Circuit breakers per agent and per provider open after 5 consecutive failures and allow one trial after 300 s.
- Graceful shutdown: the worker stops consuming, waits `SHUTDOWN_GRACE_SECONDS` for in-flight work, then checkpoints
  and hands the rest back to the queue. A restarted worker marks executions of dead runtimes as TERMINATED.

## Engines

| Engine | What runs | Notes |
|---|---|---|
| `api` | Official provider APIs through the gateway (streaming, usage, cost, fallback, routing) | [PROVIDER_INTEGRATION.md](PROVIDER_INTEGRATION.md) |
| `cli` | Claude Code, Codex CLI, Gemini CLI or OpenCode inside the sandbox, non-interactive | API keys only; image `docker/cli/Dockerfile` (build it yourself) |
| `a2a` | A remote agent over A2A v1.0 | [A2A_INTEGRATION.md](A2A_INTEGRATION.md) |
| `browser` | A Browser Bridge session (experimental) | [BROWSER_BRIDGE.md](BROWSER_BRIDGE.md) |
| `mock` | Deterministic, labelled `[MOCK — not real AI]` output | tests and demos only |

The **Agent Runtime** page shows the registered engines, executions by status and open circuits; **Executions**
shows every execution with its full timeline.
