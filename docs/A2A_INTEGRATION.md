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
- Supported methods: `SendMessage` (blocking or `returnImmediately`), `GetTask`, `ListTasks`, `CancelTask`,
  `SendStreamingMessage` and `SubscribeToTask` (card `capabilities.streaming: true`).
- **Streaming** answers `text/event-stream`. Every `data:` line is a JSON-RPC response with the request's `id` whose
  `result` is a StreamResponse: first the `task`, then `artifactUpdate` chunks of the output (`append` after the first
  chunk, `lastChunk: true` at the end) and `statusUpdate`s; the stream closes when the task is completed, failed,
  cancelled or needs input. `SubscribeToTask` replays the output produced so far after the Task, then continues; on a
  task that is already final it answers `UnsupportedOperationError` (-32004) as plain JSON. Validation errors are
  plain JSON-RPC errors too, never a stream.
- **Push notifications** (card `capabilities.pushNotifications: true`): `CreateTaskPushNotificationConfig`,
  `GetTaskPushNotificationConfig`, `ListTaskPushNotificationConfigs` (paged), `DeleteTaskPushNotificationConfig`, and
  `configuration.taskPushNotificationConfig` in `SendMessage` (its `taskId` must be empty). Configs belong to the task
  and to the key that created them; at most 10 per task. Every status change POSTs a StreamResponse to each config:
  `statusUpdate` while the task runs and the full `task` (status and artifacts) once it is final, with
  `Content-Type: application/a2a+json`, `Authorization: <scheme> <credentials>` from `authentication` and the config's
  `token` as `X-A2A-Notification-Token`. Deliveries are written in the same transaction as the status change and sent
  by the worker (role `a2a_push`) in order per config: 10 s timeout, redirects not followed, exponential backoff
  (2 s … 10 min, 8 attempts), the config is disabled after 8 consecutive failures (shown as `metadata.disabledAt`).
  Token and credentials are encrypted and never returned. Delivered rows are pruned after 7 days.
- **Webhook SSRF guard** (spec §13.2): HTTPS only, no user/password in the URL, no `localhost`/`.local`/`.internal`,
  and every address the host resolves to must be public (private, loopback, link-local incl. cloud metadata,
  IPv4-mapped forms refused) — checked when the config is created and again before each delivery.
  `A2A_PUSH_ALLOW_PRIVATE=true` relaxes this for local testing only. A host that changes its DNS between that check
  and the connection (DNS rebinding) is a residual risk.
- **Extended agent card** (`GetExtendedAgentCard`, card `capabilities.extendedAgentCard: true`; authenticated like
  every call): the public card plus one skill per enabled platform skill assigned to the agent, and the limits its
  tasks run under. No instructions, tools or prompts are disclosed.
- **Multi-turn** (§3.4): a new task in a `contextId` the key used before inherits the earlier finished turns of that
  conversation with the same agent (last 10 turns, 2 000 characters per side of a turn, 8 000 in total), fenced as
  untrusted data before the new message. `referenceTaskIds` add specific tasks (they must belong to the key and the
  agent). A message with `taskId` must reference an existing task of the key (`TaskNotFoundError` otherwise), its
  `contextId` must match (`InvalidParamsError`); a finished task is refined in a **new** task of the same context
  that references it, a running one answers `UnsupportedOperationError`. Other keys never see a conversation.
- **Input required**: when the agent asks a person (`ask.human`), the task is `TASK_STATE_INPUT_REQUIRED` with the
  question as `status.message`; a message with that `taskId` is the answer and the **same** task continues (streams
  and push notifications report it like any state). A task whose answer is in but which has not resumed yet reads as
  `TASK_STATE_WORKING`.
- Not supported yet: non-text parts.

## Using remote agents (client + `a2a` engine)

Register a provider of kind `a2a` whose base URL is the remote agent (where its agent card lives) and whose encrypted
credential is the bearer token; give an agent `execution_engine = "a2a"`. The engine submits the task, checkpoints the
remote task id immediately and derives the message id from the execution id, so a resumed execution re-attaches to
the same remote task instead of submitting a second one. When the remote card advertises streaming, the engine uses
`SendStreamingMessage` and records the output live as it arrives (cancellation is still checked every second while
the stream is idle); the number of characters already emitted is checkpointed, so a resumed run (which re-attaches by
polling) adds only the rest. Agents without streaming are polled. Remote output is untrusted data: recorded as output, never
executed. URLs must be http(s); deadlines, cancellation and retries are the runtime's.

## Verification

`backend/tests/test_a2a.py`: card, auth (missing/wrong/revoked keys, unlisted agent 404), all four methods, streaming
(event order, chunks, subscribe to a running task, errors), the client against the platform's own server (plain and
streaming), the engine streaming and polling, crash-safe re-attachment without duplicated output.
`backend/tests/test_a2a_push.py`: config methods (paging, ownership per key, final tasks), webhook payload and headers
against a local receiver, write-only secrets, retries with backoff and per-config order, disabling after failures,
pruning, and the SSRF guard (14 URL cases, plus refusal over RPC by default).
