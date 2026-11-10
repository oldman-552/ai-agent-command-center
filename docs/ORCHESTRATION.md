# Orchestration

## Projects and modes

A project has a mode — `new`, `existing` or `maintenance` — a roster of agents, an optional team and budget, and
optionally a linked GitHub repository. For existing and maintenance projects the linked repository can be imported
(cloned with the hardened git) and **analysed**: languages, frameworks, infrastructure, structure roles, dependencies,
tests and technical-debt indicators (static, nothing executed). The planner receives this map, fenced as data.

## From objective to tasks

1. **Submit** (`POST /api/workflows`, Tasks page): the objective is stored and `workflow.plan` is enqueued in the
   same transaction (outbox; an `idempotency_key` prevents double submissions).
2. **Plan** — LangGraph graph `draft → validate → persist | give_up` with a checkpoint in the `langgraph` schema. The
   planning agent is the project's agent with the `tasks.plan` permission. The reply must be JSON matching the plan
   schema (titles, descriptions, task types, required skills, acceptance criteria, dependencies, critical operation);
   it is size-capped, cycle-checked and validated against the roster's roles; invalid plans are retried with the
   error, then given up visibly.
3. **Schedule** (`workflow.advance`, stateless, run after every relevant event): tasks whose dependencies are done are
   `READY`; each is assigned to the best available agent of the project — weights: role 2, quality 2, success 1,
   load 1, cost 0.5, latency 0.3; agents with open circuits or at their concurrency limit are skipped, exhausted budgets refuse the dispatch —
   and dispatched as an execution whose prompt is built by the context assembler. Refusals (budget, circuit,
   concurrency) leave the task waiting with the reason; other tasks continue.
4. **Evaluate**: when the execution finishes, the evaluation gate runs (see below). `revise` sends the task back with
   notes (up to `max_attempts`), `reject` fails it, `approve` moves it to review.
5. **Review & approve**: a human reviews (approve / request changes / reject). If the task committed to its
   worktree, approving creates a `git.merge_protected` approval (the PR on GitHub when the project is linked); a task
   that declared a critical operation needs a `task.critical` approval. Only then is the task `COMPLETED`.

Kanban columns: BACKLOG, PLANNED, READY, IN_PROGRESS, BLOCKED, REVIEW, APPROVAL, COMPLETED, FAILED (+ CANCELLED).
Every task keeps its evidence: executions with output hashes, evaluations, reviews and approvals.

## Evaluation gate

Deterministic checks decide: non-empty output, no error, acceptance criteria coverage, tests passed / build or lint
passed when the agent ran `shell.test` / `shell.build` in the sandbox, no policy denials. An optional reviewer model
can only **withhold** approval, never grant it. Mock output can never pass a production gate unnoticed (it is
labelled). Scores feed the agents' performance profiles, which feed assignment.

## Automations

Under **Orchestration → Automations**: cron (UTC, at most every 5 minutes), interval (≥ 5 minutes) or audit-event
triggers that submit an objective or re-run the repository analysis. Run inside the worker (`automations` role, one
leader); every run is recorded; pause, resume (no catch-up), edit, run now, delete. Runs use the permissions of the
person who last saved the automation; if they lose them, runs are skipped and recorded as such. Nothing is installed
into the operating system.
