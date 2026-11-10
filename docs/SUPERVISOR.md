# Agent Supervisor

A separate worker role (`supervisor`), independent of the orchestrator; one leader across all workers (PostgreSQL
advisory lock). Every `SUPERVISOR_INTERVAL_SECONDS` it sweeps every organization.

## Detection

| Finding | Meaning |
|---|---|
| `stalled_execution` | running, but no event for longer than the stall threshold |
| `runaway_execution` | past its deadline or far beyond its expected duration |
| `excessive_retries` | too many attempts for one execution |
| `repeated_failures` | an agent failing again and again |
| `suspicious_tool_usage` | bursts of denied or unusual tool calls |
| `idle_agent_with_waiting_work` | work is waiting while a suitable agent idles |
| `deadlock` | workflow tasks waiting on each other |
| `duplicate_work` | the same task dispatched twice |
| `conflicting_changes` | worktrees of one project changing the same files |
| `retryable_task_failure` | a failed task that policy allows to retry |

Findings are deduplicated by fingerprint (one open finding per problem), carry severity and evidence, and are
resolved automatically when the problem disappears or manually by a person.

## Actions

Only a closed list, each reversible, and only those enabled in `SUPERVISOR_ACTIONS`:
`stop_runaway`, `resume_stalled` (from the last checkpoint), `isolate_agent` (open its circuit), `retry_task`,
`reduce_concurrency`. An isolated agent can be released from the UI. The supervisor **never** decides approvals,
changes permissions, merges, or touches a task that is waiting for approval — those code paths are not reachable from
it. Every finding and action is audited (`supervisor.*`) and shown on the **Agent Supervisor** page.
