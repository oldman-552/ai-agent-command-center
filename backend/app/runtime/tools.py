"""Tool catalog and security-profile ceilings (default deny — spec Phase 17).

An agent may only be granted tools allowed by its security profile. Merge, deploy and other critical
operations are deliberately NOT tools: they exist only as approval requests executed by the platform
after a human approves (Phase 16).
"""
from __future__ import annotations

TOOLS: dict[str, str] = {
    "repo.read": "Read files in the assigned worktree",
    "files.write": "Create/modify/delete files in the assigned worktree",
    "shell.test": "Run allow-listed test commands in the sandbox",
    "shell.build": "Run allow-listed build/lint commands in the sandbox",
    "git.branch": "Create the task's feature branch/worktree",
    "git.commit": "Commit to the task's feature branch",
    "pr.create": "Open a pull request from the task branch",
    "pr.review": "Comment on / review pull requests",
    "tasks.plan": "Propose tasks and dependencies",
    "tasks.assign": "Propose task assignments",
    "web.read": "Read public web pages through the platform fetcher",
    "memory.write": "Propose memories (stored as unverified until confirmed)",
}

PROFILE_CEILING: dict[str, set[str]] = {
    "read_only": {"repo.read", "web.read"},
    "reviewer": {"repo.read", "web.read", "shell.test", "pr.review", "memory.write"},
    "developer": {"repo.read", "web.read", "files.write", "shell.test", "shell.build", "git.branch", "git.commit",
                  "pr.create", "memory.write"},
    "manager": {"repo.read", "web.read", "tasks.plan", "tasks.assign", "pr.review", "memory.write"},
}


def is_tool_key(key: str) -> bool:
    return key in TOOLS or (key.startswith("mcp:") and key.count(":") == 2)


def disallowed_tools(profile: str, tools: list[str]) -> list[str]:
    """Tools outside the profile ceiling (MCP tools are checked separately against assigned servers)."""
    ceiling = PROFILE_CEILING.get(profile, set())
    return [t for t in tools if not t.startswith("mcp:") and t not in ceiling] + \
           [t for t in tools if not is_tool_key(t)]
