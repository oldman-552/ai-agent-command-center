# A2A integration

Agent-to-Agent protocol **v1.0**, JSON-RPC binding (ADR-015).

## Exposing platform agents (server)

```
GET  /api/a2a/agents/{identifier}/.well-known/agent-card.json      Agent Card
POST /api/a2a/agents/{identifier}                                   JSON-RPC 2.0, header A2A-Version: 1.0
```

- Nothing is exposed by default. A caller needs a bearer **A2A access key** (`acc_a2a_…`) that lists the agent;
  keys are created by a person with `security.manage` under **MCP & A2A → A2A access keys** and can be revoked.
  Unknown or unlisted agents answer 404 so their existence is not revealed.
- An A2A task **is** a platform execution: the state machine, tool permissions, budgets, approvals and audit apply.
- Supported methods: `SendMessage` (blocking or `returnImmediately`), `GetTask`, `ListTasks`, `CancelTask`.
  Not supported yet (answered with the spec's errors and advertised as `false` in the card): streaming, push
  notifications, the extended agent card, multi-turn follow-ups.

## Using remote agents (client + `a2a` engine)

Register a provider of kind `a2a` whose base URL is the remote agent (where its agent card lives) and whose encrypted
credential is the bearer token; give an agent `execution_engine = "a2a"`. The engine submits the task, checkpoints the
remote task id immediately and derives the message id from the execution id, so a resumed execution re-attaches to
the same remote task instead of submitting a second one. Remote output is untrusted data: recorded as output, never
executed. URLs must be http(s); deadlines, cancellation and retries are the runtime's.

## Verification

`backend/tests/test_a2a.py`: card, auth (missing/wrong/revoked keys, unlisted agent 404), all four methods, the client
against the platform's own server, crash-safe re-attachment.
