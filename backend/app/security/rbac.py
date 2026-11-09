"""Role-based access control (spec Phase 22, ADR-006).

Permissions are data (seeded into `permissions` / `role_permissions`), checked server-side on every route via
`require(...)`. Users get permissions from their role; agents never hold user roles — their capabilities come
from their security profile + tool permissions, and nothing an agent produces can change this table.
"""
from __future__ import annotations

# permission key -> description
PERMISSIONS: dict[str, str] = {
    "dashboard.read": "View dashboard and analytics",
    "projects.read": "View projects", "projects.write": "Create/edit projects", "projects.delete": "Archive/delete projects",
    "agents.read": "View agents", "agents.write": "Create/edit/clone agents",
    "agents.control": "Enable/disable/start/stop agents", "agents.delete": "Archive/delete agents",
    "agents.chat": "Chat directly with agents",
    "templates.write": "Create/edit agent templates",
    "teams.read": "View teams", "teams.write": "Manage teams",
    "tasks.read": "View tasks", "tasks.write": "Create/edit tasks", "tasks.control": "Retry/cancel/pause tasks",
    "workflows.run": "Submit objectives to the orchestrator",
    "executions.read": "View executions", "executions.control": "Cancel/resume executions",
    "providers.read": "View providers and models", "providers.manage": "Manage providers and models",
    "credentials.manage": "Create/rotate/delete credentials",
    "github.read": "View repositories, branches, PRs", "github.manage": "Connect GitHub, manage repositories",
    "approvals.read": "View approvals", "approvals.decide": "Approve/reject critical operations",
    "memory.read": "View memory", "memory.write": "Correct/delete memory",
    "skills.manage": "Install/enable skills and MCP servers",
    "browser_bridge.manage": "Manage Browser Bridge and acknowledge provider terms",
    "supervisor.read": "View supervisor findings", "supervisor.manage": "Configure supervisor policies",
    "evaluation.read": "View evaluations",
    "audit.read": "View audit logs",
    "budgets.manage": "Manage budgets",
    "automations.manage": "Manage automations",
    "users.manage": "Manage users and roles",
    "security.manage": "Change security policies",
    "settings.manage": "Change system settings",
}

_READ = [p for p in PERMISSIONS if p.endswith(".read")]

# role key -> (Arabic name, English name, permissions). "*" = every permission.
ROLES: dict[str, tuple[str, str, list[str]]] = {
    "SUPER_ADMIN": ("مدير عام", "Super Admin", ["*"]),
    "ORGANIZATION_ADMIN": ("مدير المنظمة", "Organization Admin",
                           [p for p in PERMISSIONS if p not in ("security.manage",)]),
    "PROJECT_MANAGER": ("مدير مشروع", "Project Manager", _READ + [
        "projects.write", "agents.write", "agents.control", "agents.chat", "templates.write", "teams.write",
        "tasks.write", "tasks.control", "workflows.run", "executions.control", "memory.write", "automations.manage"]),
    "DEVELOPER": ("مطوّر", "Developer", _READ + [
        "agents.chat", "tasks.write", "tasks.control", "workflows.run", "executions.control", "memory.write"]),
    "REVIEWER": ("مراجع", "Reviewer", _READ + ["agents.chat"]),
    "VIEWER": ("مشاهد", "Viewer", _READ),
}

# Permissions that only a human may ever exercise, never via an agent-originated request.
HUMAN_ONLY = {"approvals.decide", "security.manage", "users.manage", "credentials.manage", "browser_bridge.manage"}


def role_grants(role_key: str) -> set[str]:
    perms = ROLES[role_key][2]
    return set(PERMISSIONS) if perms == ["*"] else set(perms)
