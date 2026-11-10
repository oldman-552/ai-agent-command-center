"""Development-plane runtime tools: repo.read, files.write, git.branch, git.commit (GH-03, GH-04).

The runtime has already checked the tool permission. Every tool works only inside the execution's task worktree;
none of them can touch the project repository's protected branches.
"""
from __future__ import annotations

from typing import Any

from app.db import session_factory
from app.devplane import git as g
from app.devplane import worktrees as w
from app.models import Agent, Execution, Project
from app.runtime.service import register_tool
from app.security.audit import log_activity


def _tool(name: str):
    async def run(ex: Execution, args: dict[str, Any]) -> dict[str, Any]:
        with session_factory()() as db:
            try:
                wt = w.worktree_for_execution(db, ex)
                if wt is None:
                    return {"error": "no_worktree", "reason": "the execution has no project task to work on"}
                agent = db.get(Agent, ex.agent_id)
                if name == "git.branch":
                    result = {"branch": wt.branch, "base": wt.base_branch, "path": "/workspace"}
                elif name == "repo.read":
                    result = w.read_file(wt, str(args.get("path", ".")))
                elif name == "files.write":
                    result = w.write_file(wt, str(args.get("path", "")),
                                          None if args.get("delete") else str(args.get("content", "")))
                elif name == "git.commit":
                    project = db.get(Project, wt.project_id)
                    result = w.commit(db, wt, agent, str(args.get("message", "")), w.protected_branches(project))
                else:
                    return {"error": "unknown_tool"}
                if name in ("files.write", "git.commit"):
                    w.refresh(db, wt)
                    log_activity(db, f"devplane.{name.replace('.', '_')}", category="devplane",
                                 organization_id=ex.organization_id, agent_id=ex.agent_id, project_id=wt.project_id,
                                 task_id=ex.task_id, execution_id=ex.id,
                                 details={"branch": wt.branch, **{k: v for k, v in result.items() if k != "files"}})
                db.commit()
                return result
            except g.ProtectedBranch as e:
                log_activity(db, "devplane.protected_branch_blocked", category="security", level="warning",
                             organization_id=ex.organization_id, agent_id=ex.agent_id, execution_id=ex.id,
                             details={"tool": name, "reason": str(e)})
                db.commit()
                return {"error": "protected_branch", "reason": str(e)}
            except g.GitError as e:
                db.rollback()
                return {"error": "refused", "reason": str(e)}
    return run


for _name in ("repo.read", "files.write", "git.branch", "git.commit"):
    register_tool(_name, _tool(_name))
