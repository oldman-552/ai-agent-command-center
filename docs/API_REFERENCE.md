# API reference

_Generated from the FastAPI application by `scripts/gen-docs.py` — do not edit by hand._

All endpoints are under `/api`, same-origin through the web app (the Next.js server proxies `/api/*`).
Authentication: the HTTP-only session cookie set by `POST /api/auth/login`; state-changing requests must send
the `X-CSRF-Token` header equal to the `acc_csrf` cookie. Permissions are enforced on every endpoint (RBAC).
Machine clients use scoped keys instead: A2A keys (`/api/a2a/agents/*`) and MCP keys (`/api/mcp/`).
The live OpenAPI document is served at `/docs` and `/openapi.json` while the API runs.

146 operations.

## a2a

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/a2a/agents/{identifier}` | Rpc |
| `GET` | `/api/a2a/agents/{identifier}/.well-known/agent-card.json` | Get Card |
| `GET` | `/api/a2a/keys` | List Keys |
| `POST` | `/api/a2a/keys` | Create Key |
| `DELETE` | `/api/a2a/keys/{key_id}` | Revoke Key |

## agents

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/agent-templates` | List Templates |
| `POST` | `/api/agent-templates` | Create Template |
| `PATCH` | `/api/agent-templates/{template_id}` | Update Template |
| `POST` | `/api/agent-templates/{template_id}/instantiate` | Instantiate |
| `GET` | `/api/agents` | List Agents |
| `POST` | `/api/agents` | Create Agent |
| `GET` | `/api/agents-stats` | Agent Stats |
| `GET` | `/api/agents/{agent_id}` | Get Agent |
| `PATCH` | `/api/agents/{agent_id}` | Update Agent |
| `DELETE` | `/api/agents/{agent_id}` | Delete Agent — Hard delete is only allowed for archived agents (two-step, recoverable until then). |
| `POST` | `/api/agents/{agent_id}/archive` | Archive |
| `POST` | `/api/agents/{agent_id}/clone` | Clone Agent |
| `POST` | `/api/agents/{agent_id}/disable` | Disable |
| `POST` | `/api/agents/{agent_id}/enable` | Enable |
| `POST` | `/api/agents/{agent_id}/restore` | Restore |
| `POST` | `/api/agents/{agent_id}/start` | Start |
| `POST` | `/api/agents/{agent_id}/stop` | Stop |
| `GET` | `/api/agents/{agent_id}/transitions` | Transitions |
| `GET` | `/api/agents/{agent_id}/versions` | Versions |
| `GET` | `/api/tools` | List Tools |

## analytics

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/analytics/usage` | Usage |

## approvals

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/approvals` | List Approvals |
| `GET` | `/api/approvals/{approval_id}` | Get Approval |
| `POST` | `/api/approvals/{approval_id}/decide` | Decide |

## audit

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/audit` | Search |
| `GET` | `/api/audit/export.csv` | Export |

## auth

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/auth/login` | Login |
| `POST` | `/api/auth/logout` | Logout |
| `POST` | `/api/auth/logout-all` | Logout All |
| `GET` | `/api/auth/me` | Me |
| `PUT` | `/api/auth/me/language` | Set Language |

## automations

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/automations` | List Automations |
| `POST` | `/api/automations` | Create |
| `PATCH` | `/api/automations/{aid}` | Edit |
| `DELETE` | `/api/automations/{aid}` | Delete |
| `POST` | `/api/automations/{aid}/pause` | Pause |
| `POST` | `/api/automations/{aid}/resume` | Resume |
| `POST` | `/api/automations/{aid}/run` | Run Now |
| `GET` | `/api/automations/{aid}/runs` | Runs |

## browser-bridge

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/browser-bridge` | Overview |
| `PUT` | `/api/browser-bridge/enabled` | Set Enabled |
| `POST` | `/api/browser-bridge/providers/{provider}/acknowledge` | Acknowledge |
| `DELETE` | `/api/browser-bridge/providers/{provider}/acknowledge` | Revoke |
| `GET` | `/api/browser-bridge/sessions` | Sessions |
| `POST` | `/api/browser-bridge/sessions` | Create Session |
| `GET` | `/api/browser-bridge/sessions/{sid}` | Get Session |
| `POST` | `/api/browser-bridge/sessions/{sid}/cancel` | Cancel |
| `POST` | `/api/browser-bridge/sessions/{sid}/conversation` | New Conversation |
| `POST` | `/api/browser-bridge/sessions/{sid}/prompt` | Prompt |
| `POST` | `/api/browser-bridge/sessions/{sid}/recheck` | Recheck |
| `POST` | `/api/browser-bridge/sessions/{sid}/start` | Start |
| `GET` | `/api/browser-bridge/sessions/{sid}/status` | Status |
| `POST` | `/api/browser-bridge/sessions/{sid}/stop` | Stop |

## budgets

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/budgets` | List Budgets |
| `POST` | `/api/budgets` | Create Budget |
| `PATCH` | `/api/budgets/{budget_id}` | Update Budget |
| `DELETE` | `/api/budgets/{budget_id}` | Delete Budget |

## dashboard

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/dashboard/summary` | Summary |

## evaluations

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/evaluations` | Recent |
| `GET` | `/api/evaluations/profiles` | Profiles |

## executions

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/executions` | Create |
| `GET` | `/api/executions` | List Executions |
| `GET` | `/api/executions/{execution_id}` | Get Execution |
| `POST` | `/api/executions/{execution_id}/cancel` | Cancel |
| `GET` | `/api/executions/{execution_id}/events` | Events |
| `POST` | `/api/executions/{execution_id}/resume` | Resume |
| `GET` | `/api/runtime/clis` | Cli Catalog — Supported coding CLIs for the `cli` engine, the flags used and how they were verified (EE-02/EE-04). |
| `GET` | `/api/runtime/overview` | Runtime Overview — Agent Runtime page: registered engines, execution counts by status, open circuits (all from the DB). |

## github

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/github/webhook/{repository_id}` | Webhook — GH-07: unauthenticated by session, authenticated by the HMAC signature; each delivery is applied once. |
| `PUT` | `/api/projects/{project_id}/repository` | Link Project |
| `GET` | `/api/pull-requests` | List Pulls |
| `GET` | `/api/repositories` | List Repos |
| `POST` | `/api/repositories` | Connect Repo — GH-01: the token is verified against GitHub, then stored encrypted; only a 4-character hint is ever returned. |
| `GET` | `/api/worktrees` | List Worktrees |

## integrations

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/integrations/mcp-keys` | List Keys |
| `POST` | `/api/integrations/mcp-keys` | Create Key |
| `DELETE` | `/api/integrations/mcp-keys/{kid}` | Revoke Key |
| `GET` | `/api/integrations/mcp-servers` | List Servers |
| `POST` | `/api/integrations/mcp-servers` | Register Server |
| `GET` | `/api/integrations/mcp-servers/{sid}` | Get Server |
| `DELETE` | `/api/integrations/mcp-servers/{sid}` | Delete Server |
| `POST` | `/api/integrations/mcp-servers/{sid}/discover` | Discover |
| `PUT` | `/api/integrations/mcp-servers/{sid}/enabled` | Enable Server |
| `POST` | `/api/integrations/mcp-servers/{sid}/health` | Health |
| `PUT` | `/api/integrations/mcp-servers/{sid}/tools/{tid}` | Enable Tool |
| `GET` | `/api/integrations/skills` | List Skills |
| `POST` | `/api/integrations/skills` | Install Skill |
| `GET` | `/api/integrations/skills/available` | Available |
| `POST` | `/api/integrations/skills/inspect` | Inspect Skill |
| `DELETE` | `/api/integrations/skills/{kid}` | Delete Skill |
| `PUT` | `/api/integrations/skills/{kid}/agents` | Assign Skill |
| `PUT` | `/api/integrations/skills/{kid}/enabled` | Enable Skill |

## memory

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/context/tasks/{task_id}` | Preview — The context the platform would assemble for this task now (prompt + manifest; system policy shown too). |
| `GET` | `/api/memory` | List Memories |
| `POST` | `/api/memory` | Create |
| `GET` | `/api/memory/search` | Search |
| `GET` | `/api/memory/{memory_id}` | Get |
| `DELETE` | `/api/memory/{memory_id}` | Delete |
| `POST` | `/api/memory/{memory_id}/correct` | Correct |
| `POST` | `/api/memory/{memory_id}/verify` | Verify |

## orchestration

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/projects` | List Projects |
| `POST` | `/api/projects` | Create Project |
| `PATCH` | `/api/projects/{project_id}` | Update Project |
| `GET` | `/api/projects/{project_id}/analysis` | Get Analysis |
| `POST` | `/api/projects/{project_id}/analyze` | Analyze Project — OR-09: static repository map (technologies, architecture, dependencies, tests, debt). |
| `POST` | `/api/projects/{project_id}/import` | Import Repository — Existing-project mode: clone the linked GitHub repository into the project's workspace (once). |
| `GET` | `/api/tasks` | List Tasks |
| `GET` | `/api/tasks/{task_id}` | Get Task |
| `POST` | `/api/tasks/{task_id}/review` | Review Task |
| `POST` | `/api/workflows` | Submit Objective |
| `GET` | `/api/workflows` | List Workflows |
| `GET` | `/api/workflows/{workflow_id}` | Get Workflow |
| `POST` | `/api/workflows/{workflow_id}/advance` | Nudge — Ask the orchestrator to look again (e.g. after adding an agent with a missing skill). |
| `POST` | `/api/workflows/{workflow_id}/cancel` | Cancel Workflow |

## providers

| Method | Path | Summary |
|---|---|---|
| `PATCH` | `/api/models/{model_id}` | Update Model |
| `GET` | `/api/provider-kinds` | Provider Kinds |
| `GET` | `/api/providers` | List Providers |
| `POST` | `/api/providers` | Create Provider |
| `PATCH` | `/api/providers/{provider_id}` | Update Provider |
| `DELETE` | `/api/providers/{provider_id}` | Delete Provider |
| `PUT` | `/api/providers/{provider_id}/credential` | Set Credential |
| `POST` | `/api/providers/{provider_id}/discover-models` | Discover |
| `POST` | `/api/providers/{provider_id}/models` | Add Model |
| `POST` | `/api/providers/{provider_id}/test` | Test Provider |

## routing

| Method | Path | Summary |
|---|---|---|
| `POST` | `/api/routing/preview` | Preview — Which model would an auto-routed agent use for this request, and why. |

## supervisor

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/supervisor/actions` | Actions |
| `POST` | `/api/supervisor/agents/{agent_id}/release` | Release |
| `GET` | `/api/supervisor/findings` | Findings |
| `POST` | `/api/supervisor/findings/{finding_id}/resolve` | Resolve |
| `GET` | `/api/supervisor/health` | Health |
| `POST` | `/api/supervisor/run` | Run |

## system

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/health` | Health — Health of every service (UX-05): database, RabbitMQ, Redis, worker heartbeats, Browser Bridge. |

## teams

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/teams` | List Teams |
| `POST` | `/api/teams` | Create Team |
| `GET` | `/api/teams/{team_id}` | Get Team |
| `PATCH` | `/api/teams/{team_id}` | Update Team |
| `DELETE` | `/api/teams/{team_id}` | Delete Team |

## users

| Method | Path | Summary |
|---|---|---|
| `GET` | `/api/roles` | List Roles |
| `GET` | `/api/users` | List Users |
| `POST` | `/api/users` | Create User |
| `PATCH` | `/api/users/{user_id}` | Update User |

## Not in OpenAPI

| Method | Path | Summary |
|---|---|---|
| `WS` | `/api/ws` | Live updates: session cookie + CSRF value as first message; `{topic, ids}` per change |
| `POST` | `/api/mcp/` | The platform MCP server (streamable HTTP, stateless, `Authorization: Bearer acc_mcp_…`) |
