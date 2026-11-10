# Database schema

_Generated from the SQLAlchemy models by `scripts/gen-docs.py` — do not edit by hand._

PostgreSQL 16. Migrations: Alembic (`backend/alembic/versions`). LangGraph checkpoints live in the separate
`langgraph` schema. Every tenant-owned table carries `organization_id`; queries are scoped by it.

## `a2a_access_keys`

Inbound A2A access (A2A-01). Nothing is exposed by default: a key lists exactly which agents it may reach. Only a SHA-256 of the high-entropy token is stored; the token is shown once at creation.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `key_hash` | VARCHAR(64) | no | unique |
| `hint` | VARCHAR(16) | no |  |
| `agent_ids` | JSONB | no |  |
| `created_by` | INTEGER | yes | FK → `users.id` (SET NULL) |
| `last_used_at` | DATETIME | yes |  |
| `revoked_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_a2a_access_keys_created_by_users`, `fk_a2a_access_keys_organization_id_organizations`, `uq_a2a_access_keys_key_hash`

## `a2a_push_configs`

A2A push notification config (spec §4.3.1) of one inbound task. The token and credentials are encrypted and write-only; deliveries stop after repeated failures (disabled_at).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | VARCHAR(64) | no | PK |
| `execution_id` | VARCHAR(36) | no | FK → `executions.id` (CASCADE), indexed |
| `access_key_id` | INTEGER | no | FK → `a2a_access_keys.id` (CASCADE) |
| `url` | VARCHAR(2048) | no |  |
| `token_encrypted` | TEXT | yes |  |
| `auth_scheme` | VARCHAR(32) | yes |  |
| `credentials_encrypted` | TEXT | yes |  |
| `consecutive_failures` | INTEGER | no |  |
| `last_error` | VARCHAR(500) | yes |  |
| `last_delivered_at` | DATETIME | yes |  |
| `disabled_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_a2a_push_configs_access_key_id_a2a_access_keys`, `fk_a2a_push_configs_execution_id_executions`

## `a2a_push_deliveries`

One webhook POST to make (transactional outbox: written with the status change, sent by a worker).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `config_id` | VARCHAR(64) | no | FK → `a2a_push_configs.id` (CASCADE), indexed |
| `payload` | JSONB | no |  |
| `attempts` | INTEGER | no |  |
| `next_attempt_at` | DATETIME | no | indexed |
| `delivered_at` | DATETIME | yes |  |
| `abandoned_at` | DATETIME | yes |  |
| `last_status` | INTEGER | yes |  |
| `created_at` | DATETIME | no |  |

Constraints: `fk_a2a_push_deliveries_config_id_a2a_push_configs`

## `agent_config_versions`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `agent_id` | INTEGER | no | FK → `agents.id` (CASCADE), indexed |
| `version` | INTEGER | no |  |
| `snapshot` | JSONB | no |  |
| `changed_by_user_id` | INTEGER | yes | FK → `users.id` |
| `created_at` | DATETIME | no |  |

Constraints: `fk_agent_config_versions_agent_id_agents`, `fk_agent_config_versions_changed_by_user_id_users`, `uq_agent_config_versions_agent_id`

## `agent_messages`

Structured message contract (Phase 6).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `message_id` | VARCHAR(36) | no | unique |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `sender` | VARCHAR(128) | no |  |
| `recipient` | VARCHAR(128) | no |  |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL) |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `session_id` | INTEGER | yes | FK → `agent_sessions.id` (CASCADE), indexed |
| `correlation_id` | VARCHAR(64) | yes | indexed |
| `message_type` | VARCHAR(32) | no |  |
| `payload` | JSONB | no |  |
| `priority` | INTEGER | no |  |
| `schema_version` | VARCHAR(8) | no |  |
| `is_mock` | BOOLEAN | no |  |
| `ts` | DATETIME | no | indexed |

Constraints: `fk_agent_messages_organization_id_organizations`, `fk_agent_messages_project_id_projects`, `fk_agent_messages_session_id_agent_sessions`, `fk_agent_messages_task_id_tasks`, `uq_agent_messages_message_id`

## `agent_sessions`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `agent_id` | INTEGER | no | FK → `agents.id` (CASCADE), indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL) |
| `user_id` | INTEGER | yes | FK → `users.id` |
| `kind` | VARCHAR(16) | no |  |
| `status` | VARCHAR(16) | no |  |
| `started_at` | DATETIME | no |  |
| `ended_at` | DATETIME | yes |  |

Constraints: `fk_agent_sessions_agent_id_agents`, `fk_agent_sessions_project_id_projects`, `fk_agent_sessions_user_id_users`

## `agent_state_transitions`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `agent_id` | INTEGER | no | FK → `agents.id` (CASCADE), indexed |
| `from_state` | VARCHAR(32) | yes |  |
| `to_state` | VARCHAR(32) | no |  |
| `reason` | TEXT | no |  |
| `actor` | VARCHAR(64) | no |  |
| `ts` | DATETIME | no | indexed |

Constraints: `fk_agent_state_transitions_agent_id_agents`

## `agent_templates`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | yes | FK → `organizations.id` (CASCADE) |
| `key` | VARCHAR(64) | no | unique |
| `name_ar` | VARCHAR(128) | no |  |
| `name_en` | VARCHAR(128) | no |  |
| `role` | VARCHAR(64) | no |  |
| `description_ar` | TEXT | no |  |
| `description_en` | TEXT | no |  |
| `system_instructions` | TEXT | no |  |
| `tool_permissions` | JSONB | no |  |
| `skills` | JSONB | no |  |
| `security_profile` | VARCHAR(16) | no |  |
| `context_policy` | JSONB | no |  |
| `memory_profile` | JSONB | no |  |
| `is_system` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_agent_templates_security_profile_valid`, `fk_agent_templates_organization_id_organizations`, `uq_agent_templates_key`

## `agents`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `identifier` | VARCHAR(64) | no |  |
| `name` | VARCHAR(128) | no |  |
| `description` | TEXT | no |  |
| `role` | VARCHAR(64) | no |  |
| `avatar` | VARCHAR(64) | yes |  |
| `template_id` | INTEGER | yes | FK → `agent_templates.id` (SET NULL) |
| `system_instructions` | TEXT | no |  |
| `provider_id` | INTEGER | yes | FK → `providers.id` (SET NULL) |
| `model` | VARCHAR(128) | yes |  |
| `fallback_provider_id` | INTEGER | yes | FK → `providers.id` (SET NULL) |
| `fallback_model` | VARCHAR(128) | yes |  |
| `execution_engine` | VARCHAR(16) | no |  |
| `tool_permissions` | JSONB | no |  |
| `skills` | JSONB | no |  |
| `mcp_server_ids` | JSONB | no |  |
| `memory_profile` | JSONB | no |  |
| `context_policy` | JSONB | no |  |
| `security_profile` | VARCHAR(16) | no |  |
| `token_budget` | BIGINT | yes |  |
| `cost_budget_usd` | NUMERIC(12, 4) | yes |  |
| `timeout_seconds` | INTEGER | no |  |
| `retry_policy` | JSONB | no |  |
| `concurrency_limit` | INTEGER | no |  |
| `priority` | INTEGER | no |  |
| `supervisor_agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `state` | VARCHAR(32) | no |  |
| `is_enabled` | BOOLEAN | no |  |
| `archived_at` | DATETIME | yes |  |
| `config_version` | INTEGER | no |  |
| `last_error` | TEXT | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_agents_concurrency_positive`, `ck_agents_execution_engine_valid`, `ck_agents_security_profile_valid`, `ck_agents_state_valid`, `ck_agents_timeout_positive`, `fk_agents_fallback_provider_id_providers`, `fk_agents_organization_id_organizations`, `fk_agents_provider_id_providers`, `fk_agents_supervisor_agent_id_agents`, `fk_agents_template_id_agent_templates`, `uq_agents_organization_id`

## `api_credentials`

Encrypted secret. Plaintext never leaves the backend and never enters a prompt.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `kind` | VARCHAR(32) | no |  |
| `encrypted_value` | TEXT | no |  |
| `hint` | VARCHAR(16) | no |  |
| `rotated_at` | DATETIME | yes |  |
| `created_by_user_id` | INTEGER | yes | FK → `users.id` |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_api_credentials_created_by_user_id_users`, `fk_api_credentials_organization_id_organizations`

## `approvals`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `operation` | VARCHAR(64) | no |  |
| `risk_level` | VARCHAR(16) | no |  |
| `status` | VARCHAR(32) | no | indexed |
| `title` | VARCHAR(255) | no |  |
| `summary` | TEXT | no |  |
| `payload` | JSONB | no |  |
| `evidence` | JSONB | no |  |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL) |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `execution_id` | VARCHAR(36) | yes | FK → `executions.id` (SET NULL) |
| `pull_request_id` | INTEGER | yes | FK → `pull_requests.id` (SET NULL) |
| `requested_by` | VARCHAR(64) | no |  |
| `approver_id` | INTEGER | yes | FK → `users.id` |
| `decided_at` | DATETIME | yes |  |
| `reason` | TEXT | yes |  |
| `executed_at` | DATETIME | yes |  |
| `idempotency_key` | VARCHAR(128) | yes | unique |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_approvals_risk_level_valid`, `ck_approvals_status_valid`, `fk_approvals_agent_id_agents`, `fk_approvals_approver_id_users`, `fk_approvals_execution_id_executions`, `fk_approvals_organization_id_organizations`, `fk_approvals_project_id_projects`, `fk_approvals_pull_request_id_pull_requests`, `fk_approvals_task_id_tasks`, `uq_approvals_idempotency_key`

## `audit_logs`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `organization_id` | INTEGER | yes | FK → `organizations.id` (CASCADE), indexed |
| `ts` | DATETIME | no | indexed |
| `actor` | VARCHAR(64) | no |  |
| `user_id` | INTEGER | yes | FK → `users.id` |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL) |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `execution_id` | VARCHAR(36) | yes | FK → `executions.id` (SET NULL) |
| `action` | VARCHAR(64) | no | indexed |
| `category` | VARCHAR(32) | no | indexed |
| `level` | VARCHAR(16) | no |  |
| `message` | TEXT | no |  |
| `details` | JSONB | no |  |
| `tokens` | BIGINT | no |  |
| `cost_usd` | NUMERIC(12, 6) | yes |  |

Constraints: `fk_audit_logs_agent_id_agents`, `fk_audit_logs_execution_id_executions`, `fk_audit_logs_organization_id_organizations`, `fk_audit_logs_project_id_projects`, `fk_audit_logs_task_id_tasks`, `fk_audit_logs_user_id_users`

## `automation_runs`

One execution of an automation (UX-06 history).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `automation_id` | INTEGER | no | FK → `automations.id` (CASCADE), indexed |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `trigger` | VARCHAR(32) | no |  |
| `status` | VARCHAR(16) | no |  |
| `detail` | TEXT | no |  |
| `result` | JSONB | no |  |
| `started_at` | DATETIME | no |  |
| `finished_at` | DATETIME | yes |  |

Constraints: `fk_automation_runs_automation_id_automations`, `fk_automation_runs_organization_id_organizations`

## `automations`

In-app scheduled/event automations. Never installed as OS services (Phase 24).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (CASCADE) |
| `name` | VARCHAR(128) | no |  |
| `trigger` | JSONB | no |  |
| `action` | JSONB | no |  |
| `status` | VARCHAR(16) | no |  |
| `last_run_at` | DATETIME | yes |  |
| `next_run_at` | DATETIME | yes |  |
| `state` | JSONB | no |  |
| `created_by_user_id` | INTEGER | yes | FK → `users.id` |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_automations_created_by_user_id_users`, `fk_automations_organization_id_organizations`, `fk_automations_project_id_projects`

## `browser_sessions`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `provider_key` | VARCHAR(32) | no |  |
| `profile_name` | VARCHAR(64) | no |  |
| `state` | VARCHAR(16) | no |  |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `conversation_ref` | VARCHAR(255) | yes |  |
| `history` | JSONB | no |  |
| `model` | VARCHAR(128) | yes |  |
| `last_activity_at` | DATETIME | yes |  |
| `error` | TEXT | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_browser_sessions_state_valid`, `fk_browser_sessions_agent_id_agents`, `fk_browser_sessions_organization_id_organizations`

## `budgets`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `scope` | VARCHAR(16) | no |  |
| `scope_id` | INTEGER | yes |  |
| `period` | VARCHAR(16) | no |  |
| `limit_usd` | NUMERIC(12, 4) | yes |  |
| `limit_tokens` | BIGINT | yes |  |
| `enabled` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_budgets_organization_id_organizations`

## `checkpoints`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `execution_id` | VARCHAR(36) | no | FK → `executions.id` (CASCADE), indexed |
| `seq` | INTEGER | no |  |
| `state` | JSONB | no |  |
| `verified` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |

Constraints: `fk_checkpoints_execution_id_executions`, `uq_checkpoints_execution_id`

## `circuit_breakers`

Circuit breakers for agents and providers (EV-03). closed → open after N consecutive failures; after the cool-down one trial is let through (half_open); its success closes the circuit, its failure re-opens it.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `organization_id` | INTEGER | no | indexed |
| `scope` | VARCHAR(16) | no |  |
| `ref_id` | INTEGER | no |  |
| `state` | VARCHAR(16) | no |  |
| `consecutive_failures` | INTEGER | no |  |
| `opened_at` | DATETIME | yes |  |
| `last_failure` | TEXT | yes |  |
| `updated_at` | DATETIME | no |  |

Constraints: `uq_circuit_breakers_scope`

## `cost_records`

Cost derived from a usage record and owner-entered prices. Absent when the price is unknown.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `usage_record_id` | BIGINT | no | FK → `usage_records.id` (CASCADE), unique |
| `ts` | DATETIME | no | indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL), indexed |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL), indexed |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `amount_usd` | NUMERIC(12, 6) | no |  |
| `pricing` | JSONB | no |  |

Constraints: `fk_cost_records_agent_id_agents`, `fk_cost_records_organization_id_organizations`, `fk_cost_records_project_id_projects`, `fk_cost_records_task_id_tasks`, `fk_cost_records_usage_record_id_usage_records`, `uq_cost_records_usage_record_id`

## `evaluation_results`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `execution_id` | VARCHAR(36) | yes | FK → `executions.id` (SET NULL), indexed |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL), indexed |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL), indexed |
| `provider` | VARCHAR(64) | yes |  |
| `model` | VARCHAR(128) | yes |  |
| `task_type` | VARCHAR(32) | yes |  |
| `scores` | JSONB | no |  |
| `overall` | FLOAT | yes |  |
| `decision` | VARCHAR(16) | no |  |
| `evidence` | JSONB | no |  |
| `created_at` | DATETIME | no | indexed |

Constraints: `fk_evaluation_results_agent_id_agents`, `fk_evaluation_results_execution_id_executions`, `fk_evaluation_results_organization_id_organizations`, `fk_evaluation_results_task_id_tasks`

## `execution_events`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `execution_id` | VARCHAR(36) | no | FK → `executions.id` (CASCADE), indexed |
| `seq` | INTEGER | no |  |
| `ts` | DATETIME | no |  |
| `type` | VARCHAR(32) | no |  |
| `payload` | JSONB | no |  |

Constraints: `fk_execution_events_execution_id_executions`, `uq_execution_events_execution_id`

## `executions`

The execution contract of spec Phase 2 — one row per agent run.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | VARCHAR(36) | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL), indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL), indexed |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL), indexed |
| `runtime_id` | VARCHAR(64) | yes |  |
| `provider` | VARCHAR(64) | yes |  |
| `model` | VARCHAR(128) | yes |  |
| `engine` | VARCHAR(16) | no |  |
| `input_context` | JSONB | no |  |
| `tool_permissions` | JSONB | no |  |
| `status` | VARCHAR(32) | no | indexed |
| `started_at` | DATETIME | yes |  |
| `ended_at` | DATETIME | yes |  |
| `deadline_at` | DATETIME | yes |  |
| `output` | JSONB | yes |  |
| `input_tokens` | BIGINT | yes |  |
| `output_tokens` | BIGINT | yes |  |
| `cached_tokens` | BIGINT | yes |  |
| `cost_usd` | NUMERIC(12, 6) | yes |  |
| `error` | TEXT | yes |  |
| `error_category` | VARCHAR(32) | yes |  |
| `evidence` | JSONB | no |  |
| `attempt` | INTEGER | no |  |
| `idempotency_key` | VARCHAR(128) | yes | unique |
| `parent_execution_id` | VARCHAR(36) | yes | FK → `executions.id` (SET NULL) |
| `latest_checkpoint_seq` | INTEGER | yes |  |
| `cancel_requested` | BOOLEAN | no |  |
| `is_mock` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_executions_status_valid`, `fk_executions_agent_id_agents`, `fk_executions_organization_id_organizations`, `fk_executions_parent_execution_id_executions`, `fk_executions_project_id_projects`, `fk_executions_task_id_tasks`, `uq_executions_idempotency_key`

## `mcp_access_keys`

Inbound access to the platform's MCP server (MCP-02). A key lists exactly which platform tools it may call; only a SHA-256 of the token is stored and the token is shown once.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `key_hash` | VARCHAR(64) | no | unique |
| `hint` | VARCHAR(16) | no |  |
| `tools` | JSONB | no |  |
| `created_by` | INTEGER | yes | FK → `users.id` (SET NULL) |
| `last_used_at` | DATETIME | yes |  |
| `revoked_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_mcp_access_keys_created_by_users`, `fk_mcp_access_keys_organization_id_organizations`, `uq_mcp_access_keys_key_hash`

## `mcp_servers`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `transport` | VARCHAR(16) | no |  |
| `command` | JSONB | yes |  |
| `url` | VARCHAR(512) | yes |  |
| `credential_id` | INTEGER | yes | FK → `api_credentials.id` (SET NULL) |
| `enabled` | BOOLEAN | no |  |
| `status` | VARCHAR(16) | no |  |
| `security_assessment` | JSONB | yes |  |
| `last_checked_at` | DATETIME | yes |  |
| `last_error` | TEXT | yes |  |
| `resources` | JSONB | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_mcp_servers_credential_id_api_credentials`, `fk_mcp_servers_organization_id_organizations`, `uq_mcp_servers_organization_id`

## `mcp_tools`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `server_id` | INTEGER | no | FK → `mcp_servers.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `description` | TEXT | no |  |
| `input_schema` | JSONB | no |  |
| `permission_key` | VARCHAR(128) | no |  |
| `enabled` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_mcp_tools_server_id_mcp_servers`, `uq_mcp_tools_server_id`

## `memories`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (CASCADE), indexed |
| `agent_id` | INTEGER | yes | FK → `agents.id` (CASCADE), indexed |
| `team_id` | INTEGER | yes | FK → `teams.id` (CASCADE) |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `memory_type` | VARCHAR(32) | no |  |
| `title` | VARCHAR(255) | no |  |
| `content` | TEXT | no |  |
| `source` | JSONB | no |  |
| `verification` | VARCHAR(16) | no |  |
| `version` | INTEGER | no |  |
| `supersedes_id` | INTEGER | yes | FK → `memories.id` (SET NULL) |
| `is_current` | BOOLEAN | no |  |
| `expires_at` | DATETIME | yes |  |
| `embedding` | ARRAY | yes |  |
| `semantic_embedding` | ARRAY | yes |  |
| `semantic_model` | VARCHAR(160) | yes |  |
| `tsv` | TSVECTOR | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_memories_memory_type_valid`, `ck_memories_verification_valid`, `fk_memories_agent_id_agents`, `fk_memories_organization_id_organizations`, `fk_memories_project_id_projects`, `fk_memories_supersedes_id_memories`, `fk_memories_task_id_tasks`, `fk_memories_team_id_teams`

## `models`

A model offered by a provider. Prices are owner-entered; NULL means unknown (never invented).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `provider_id` | INTEGER | no | FK → `providers.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `display_name` | VARCHAR(128) | yes |  |
| `context_window` | INTEGER | yes |  |
| `max_output_tokens` | INTEGER | yes |  |
| `capabilities` | JSONB | no |  |
| `tier` | INTEGER | no |  |
| `input_price_per_mtok` | NUMERIC(12, 6) | yes |  |
| `output_price_per_mtok` | NUMERIC(12, 6) | yes |  |
| `cached_input_price_per_mtok` | NUMERIC(12, 6) | yes |  |
| `enabled` | BOOLEAN | no |  |
| `discovered` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_models_provider_id_providers`, `uq_models_provider_id`

## `organizations`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `slug` | VARCHAR(64) | no | unique |
| `name` | VARCHAR(128) | no |  |
| `settings` | JSONB | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `uq_organizations_slug`

## `outbox_messages`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `message_id` | VARCHAR(36) | no | unique |
| `exchange` | VARCHAR(64) | no |  |
| `routing_key` | VARCHAR(128) | no |  |
| `envelope` | JSONB | no |  |
| `trace_context` | JSONB | yes |  |
| `priority` | INTEGER | no |  |
| `attempts` | INTEGER | no |  |
| `last_error` | TEXT | yes |  |
| `created_at` | DATETIME | no |  |
| `published_at` | DATETIME | yes | indexed |

Constraints: `uq_outbox_messages_message_id`

## `permissions`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `key` | VARCHAR(64) | no | unique |
| `description` | TEXT | no |  |

Constraints: `uq_permissions_key`

## `processed_messages`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `consumer` | VARCHAR(64) | no |  |
| `idempotency_key` | VARCHAR(160) | no |  |
| `message_id` | VARCHAR(36) | no |  |
| `outcome` | VARCHAR(16) | no |  |
| `processed_at` | DATETIME | no |  |

Constraints: `uq_processed_messages_consumer`

## `project_agents`

The type of the None singleton.

| Column | Type | Null | Notes |
|---|---|---|---|
| `project_id` | INTEGER | no | PK, FK → `projects.id` (CASCADE) |
| `agent_id` | INTEGER | no | PK, FK → `agents.id` (CASCADE) |

Constraints: `fk_project_agents_agent_id_agents`, `fk_project_agents_project_id_projects`

## `projects`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `team_id` | INTEGER | yes | FK → `teams.id` (SET NULL) |
| `name` | VARCHAR(128) | no |  |
| `description` | TEXT | no |  |
| `mode` | VARCHAR(16) | no |  |
| `repository_id` | INTEGER | yes | FK → `repositories.id` (SET NULL) |
| `default_branch` | VARCHAR(255) | no |  |
| `budget_usd` | NUMERIC(12, 4) | yes |  |
| `analysis` | JSONB | yes |  |
| `analyzed_at` | DATETIME | yes |  |
| `archived_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_projects_mode_valid`, `fk_projects_organization_id_organizations`, `fk_projects_repository_id_repositories`, `fk_projects_team_id_teams`, `uq_projects_organization_id`

## `providers`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `kind` | VARCHAR(32) | no |  |
| `is_mock` | BOOLEAN | no |  |
| `enabled` | BOOLEAN | no |  |
| `auth_type` | VARCHAR(16) | no |  |
| `base_url` | VARCHAR(512) | yes |  |
| `credential_id` | INTEGER | yes | FK → `api_credentials.id` (SET NULL) |
| `status` | VARCHAR(16) | no |  |
| `last_tested_at` | DATETIME | yes |  |
| `last_error` | TEXT | yes |  |
| `rate_limits` | JSONB | no |  |
| `priority` | INTEGER | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_providers_auth_type_valid`, `fk_providers_credential_id_api_credentials`, `fk_providers_organization_id_organizations`, `uq_providers_organization_id`

## `pull_requests`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `repository_id` | INTEGER | no | FK → `repositories.id` (CASCADE) |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `number` | INTEGER | no |  |
| `url` | VARCHAR(512) | no |  |
| `title` | VARCHAR(255) | no |  |
| `head_branch` | VARCHAR(255) | no |  |
| `base_branch` | VARCHAR(255) | no |  |
| `state` | VARCHAR(32) | no |  |
| `checks` | JSONB | yes |  |
| `merged_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_pull_requests_organization_id_organizations`, `fk_pull_requests_repository_id_repositories`, `fk_pull_requests_task_id_tasks`, `uq_pull_requests_repository_id`

## `repositories`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `host` | VARCHAR(32) | no |  |
| `full_name` | VARCHAR(255) | no |  |
| `default_branch` | VARCHAR(255) | no |  |
| `protected_branches` | JSONB | no |  |
| `is_private` | BOOLEAN | no |  |
| `credential_id` | INTEGER | yes | FK → `api_credentials.id` (SET NULL) |
| `analysis` | JSONB | yes |  |
| `api_base` | VARCHAR(255) | no |  |
| `webhook_credential_id` | INTEGER | yes | FK → `api_credentials.id` (SET NULL) |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_repositories_credential_id_api_credentials`, `fk_repositories_organization_id_organizations`, `fk_repositories_webhook_credential_id_api_credentials`, `uq_repositories_organization_id`

## `role_permissions`

The type of the None singleton.

| Column | Type | Null | Notes |
|---|---|---|---|
| `role_id` | INTEGER | no | PK, FK → `roles.id` (CASCADE) |
| `permission_id` | INTEGER | no | PK, FK → `permissions.id` (CASCADE) |

Constraints: `fk_role_permissions_permission_id_permissions`, `fk_role_permissions_role_id_roles`

## `roles`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `key` | VARCHAR(64) | no | unique |
| `name_ar` | VARCHAR(128) | no |  |
| `name_en` | VARCHAR(128) | no |  |
| `is_system` | BOOLEAN | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `uq_roles_key`

## `skills`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `key` | VARCHAR(128) | no |  |
| `name` | VARCHAR(128) | no |  |
| `source` | VARCHAR(512) | no |  |
| `version` | VARCHAR(64) | yes |  |
| `license` | VARCHAR(64) | yes |  |
| `instructions` | TEXT | no |  |
| `assessment` | JSONB | yes |  |
| `enabled` | BOOLEAN | no |  |
| `agent_ids` | JSONB | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_skills_organization_id_organizations`, `uq_skills_organization_id`

## `supervisor_findings`

Something the supervisor noticed (SV-02/SV-04) and what, if anything, it did about it (SV-03).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `organization_id` | INTEGER | no | indexed |
| `kind` | VARCHAR(48) | no | indexed |
| `severity` | VARCHAR(16) | no |  |
| `subject_type` | VARCHAR(24) | no |  |
| `subject_id` | VARCHAR(64) | no |  |
| `fingerprint` | VARCHAR(160) | no | unique |
| `details` | JSONB | no |  |
| `recommendation` | TEXT | no |  |
| `action` | JSONB | yes |  |
| `status` | VARCHAR(16) | no | indexed |
| `detected_at` | DATETIME | no |  |
| `last_seen_at` | DATETIME | no |  |
| `resolved_at` | DATETIME | yes |  |

Constraints: `uq_supervisor_findings_fingerprint`

## `system_settings`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | yes | FK → `organizations.id` (CASCADE) |
| `key` | VARCHAR(128) | no |  |
| `value` | JSONB | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_system_settings_organization_id_organizations`, `uq_system_settings_organization_id`

## `task_dependencies`

| Column | Type | Null | Notes |
|---|---|---|---|
| `task_id` | INTEGER | no | PK, FK → `tasks.id` (CASCADE) |
| `depends_on_id` | INTEGER | no | PK, FK → `tasks.id` (CASCADE) |

Constraints: `ck_task_dependencies_no_self_dependency`, `fk_task_dependencies_depends_on_id_tasks`, `fk_task_dependencies_task_id_tasks`

## `tasks`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (CASCADE), indexed |
| `workflow_id` | INTEGER | yes | FK → `workflows.id` (SET NULL), indexed |
| `parent_task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `title` | VARCHAR(255) | no |  |
| `description` | TEXT | no |  |
| `task_type` | VARCHAR(32) | no |  |
| `required_skills` | JSONB | no |  |
| `status` | VARCHAR(16) | no | indexed |
| `priority` | INTEGER | no |  |
| `assigned_agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL), indexed |
| `acceptance_criteria` | JSONB | no |  |
| `budget_usd` | NUMERIC(12, 4) | yes |  |
| `estimated_effort` | VARCHAR(32) | yes |  |
| `evidence` | JSONB | no |  |
| `review_status` | VARCHAR(16) | no |  |
| `critical_operation` | VARCHAR(64) | yes |  |
| `result` | JSONB | yes |  |
| `error` | TEXT | yes |  |
| `attempts` | INTEGER | no |  |
| `max_attempts` | INTEGER | no |  |
| `idempotency_key` | VARCHAR(128) | yes | unique |
| `branch` | VARCHAR(255) | yes |  |
| `deadline` | DATETIME | yes |  |
| `created_by_user_id` | INTEGER | yes | FK → `users.id` |
| `created_by_agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `started_at` | DATETIME | yes |  |
| `finished_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `ck_tasks_max_attempts_positive`, `ck_tasks_status_valid`, `fk_tasks_assigned_agent_id_agents`, `fk_tasks_created_by_agent_id_agents`, `fk_tasks_created_by_user_id_users`, `fk_tasks_organization_id_organizations`, `fk_tasks_parent_task_id_tasks`, `fk_tasks_project_id_projects`, `fk_tasks_workflow_id_workflows`, `uq_tasks_idempotency_key`

## `team_agents`

The type of the None singleton.

| Column | Type | Null | Notes |
|---|---|---|---|
| `team_id` | INTEGER | no | PK, FK → `teams.id` (CASCADE) |
| `agent_id` | INTEGER | no | PK, FK → `agents.id` (CASCADE) |

Constraints: `fk_team_agents_agent_id_agents`, `fk_team_agents_team_id_teams`

## `team_users`

The type of the None singleton.

| Column | Type | Null | Notes |
|---|---|---|---|
| `team_id` | INTEGER | no | PK, FK → `teams.id` (CASCADE) |
| `user_id` | INTEGER | no | PK, FK → `users.id` (CASCADE) |

Constraints: `fk_team_users_team_id_teams`, `fk_team_users_user_id_users`

## `teams`

A team of humans and/or agents (Agent Teams page).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `name` | VARCHAR(128) | no |  |
| `description` | TEXT | no |  |
| `lead_agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `policies` | JSONB | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_teams_lead_agent_id_agents`, `fk_teams_organization_id_organizations`, `uq_teams_organization_id`

## `usage_records`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGINT | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `ts` | DATETIME | no | indexed |
| `provider_id` | INTEGER | yes | FK → `providers.id` (SET NULL) |
| `provider_kind` | VARCHAR(32) | no |  |
| `model` | VARCHAR(128) | no |  |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL), indexed |
| `project_id` | INTEGER | yes | FK → `projects.id` (SET NULL), indexed |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL) |
| `execution_id` | VARCHAR(36) | yes | FK → `executions.id` (SET NULL), indexed |
| `input_tokens` | BIGINT | no |  |
| `output_tokens` | BIGINT | no |  |
| `cached_tokens` | BIGINT | no |  |
| `duration_ms` | INTEGER | yes |  |
| `retry_count` | INTEGER | no |  |
| `is_mock` | BOOLEAN | no |  |

Constraints: `fk_usage_records_agent_id_agents`, `fk_usage_records_execution_id_executions`, `fk_usage_records_organization_id_organizations`, `fk_usage_records_project_id_projects`, `fk_usage_records_provider_id_providers`, `fk_usage_records_task_id_tasks`

## `users`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `username` | VARCHAR(64) | no | unique |
| `email` | VARCHAR(255) | yes |  |
| `password_hash` | VARCHAR(255) | no |  |
| `role_id` | INTEGER | no | FK → `roles.id` |
| `is_active` | BOOLEAN | no |  |
| `language` | VARCHAR(5) | no |  |
| `token_version` | INTEGER | no |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_users_organization_id_organizations`, `fk_users_role_id_roles`, `uq_users_username`

## `workflows`

One user objective planned by the orchestrator into a task graph.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `project_id` | INTEGER | no | FK → `projects.id` (CASCADE), indexed |
| `title` | VARCHAR(255) | no |  |
| `objective` | TEXT | no |  |
| `status` | VARCHAR(32) | no |  |
| `plan` | JSONB | yes |  |
| `thread_id` | VARCHAR(64) | yes | unique |
| `created_by_user_id` | INTEGER | yes | FK → `users.id` |
| `error` | TEXT | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_workflows_created_by_user_id_users`, `fk_workflows_organization_id_organizations`, `fk_workflows_project_id_projects`, `uq_workflows_thread_id`

## `worktrees`

An agent's isolated git worktree + feature branch for one task (GH-03, spec Phase 12).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | INTEGER | no | PK |
| `organization_id` | INTEGER | no | FK → `organizations.id` (CASCADE), indexed |
| `project_id` | INTEGER | no | FK → `projects.id` (CASCADE), indexed |
| `task_id` | INTEGER | yes | FK → `tasks.id` (SET NULL), indexed |
| `agent_id` | INTEGER | yes | FK → `agents.id` (SET NULL) |
| `branch` | VARCHAR(255) | no |  |
| `base_branch` | VARCHAR(255) | no |  |
| `base_commit` | VARCHAR(64) | no |  |
| `head_commit` | VARCHAR(64) | yes |  |
| `path` | VARCHAR(1024) | no |  |
| `status` | VARCHAR(16) | no |  |
| `modified_files` | JSONB | no |  |
| `conflicts` | JSONB | no |  |
| `commits` | JSONB | no |  |
| `merged_at` | DATETIME | yes |  |
| `created_at` | DATETIME | no |  |
| `updated_at` | DATETIME | no |  |

Constraints: `fk_worktrees_agent_id_agents`, `fk_worktrees_organization_id_organizations`, `fk_worktrees_project_id_projects`, `fk_worktrees_task_id_tasks`, `uq_worktrees_project_id`
