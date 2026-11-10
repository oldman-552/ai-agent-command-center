"""Per-task worktrees, file tools and approved merges (GH-03, GH-04, GH-06, ADR-021).

Layout under the project's sandbox root (`<WORKSPACES_DIR>/<org>/<project>`):
    repo/                 the project repository, default branch checked out — only approved merges touch it
    wt/task-<id>/         one worktree per task on branch `agent/<agent-identifier>/task-<id>`
The sandbox shell tools mount the task's worktree, so tests run against exactly the agent's changes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.devplane import git as g
from app.models import Agent, Approval, Execution, Project, Task, Worktree
from app.sandbox import runner
from app.security.audit import log_activity

MAX_FILE_BYTES = 1024 * 1024


def protected_branches(project: Project) -> list[str]:
    repo_rule = project.repository.protected_branches if project.repository else None
    return sorted({*(repo_rule or ["main", "master"]), project.default_branch})


def repo_path(organization_id: int, project_id: int) -> Path:
    return runner.ensure_workspace(organization_id, project_id) / "repo"


def ensure_repo(project: Project) -> Path:
    path = repo_path(project.organization_id, project.id)
    g.init_repo(path, project.default_branch)
    runner.ensure_workspace(project.organization_id, project.id)  # keep everything owned by the sandbox user
    return path


def branch_for(agent: Agent, task: Task) -> str:
    return f"agent/{agent.identifier}/task-{task.id}"


def ensure_worktree(db: Session, task: Task, agent: Agent) -> Worktree:
    """The task's worktree (created on first use). Idempotent."""
    wt = db.scalar(select(Worktree).where(Worktree.task_id == task.id, Worktree.status.in_(["active", "committed"])))
    if wt is not None:
        return wt
    project = db.get(Project, task.project_id)
    repo = ensure_repo(project)
    branch = branch_for(agent, task)
    if not g.valid_branch(branch):
        raise g.GitError(f"invalid branch name {branch!r}")
    g.assert_not_protected(branch, protected_branches(project))
    path = repo.parent / "wt" / f"task-{task.id}"
    base = g.git(repo, "rev-parse", project.default_branch)
    if not path.exists():
        existing = g.git(repo, "branch", "--list", branch)
        g.git(repo, "worktree", "add", "-q", *([] if existing else ["-b", branch]), str(path),
              *([branch] if existing else [project.default_branch]))
    runner.ensure_workspace(project.organization_id, project.id)
    wt = Worktree(organization_id=task.organization_id, project_id=project.id, task_id=task.id, agent_id=agent.id,
                  branch=branch, base_branch=project.default_branch, base_commit=base, path=str(path))
    db.add(wt)
    task.branch = branch
    db.flush()
    log_activity(db, "devplane.worktree_created", category="devplane", organization_id=task.organization_id,
                 agent_id=agent.id, project_id=project.id, task_id=task.id, details={"branch": branch})
    return wt


def _safe_path(wt: Worktree, rel: str) -> Path:
    """A path inside the worktree that is not git metadata and does not escape through a symlink."""
    if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
        raise g.GitError("path must be relative to the worktree")
    parts = Path(rel).parts
    if ".." in parts or ".git" in parts:
        raise g.GitError("path outside the worktree or inside .git")
    root = Path(wt.path).resolve()
    target = (root / rel)
    real_parent = target.parent.resolve()
    if real_parent != root and root not in real_parent.parents:
        raise g.GitError("path escapes the worktree")
    if target.is_symlink():
        raise g.GitError("refusing to follow a symlink")
    return target


def write_file(wt: Worktree, rel: str, content: str | None) -> dict[str, Any]:
    target = _safe_path(wt, rel)
    if content is None:
        if target.exists():
            target.unlink()
        return {"deleted": rel}
    data = content.encode()
    if len(data) > MAX_FILE_BYTES:
        raise g.GitError("file too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"written": rel, "bytes": len(data)}


def read_file(wt: Worktree, rel: str) -> dict[str, Any]:
    target = _safe_path(wt, rel or ".")  if rel not in ("", ".") else Path(wt.path)
    if target.is_dir():
        entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir() if p.name != ".git")
        return {"path": rel or ".", "entries": entries[:500]}
    if not target.exists():
        return {"error": "not_found", "path": rel}
    data = target.read_bytes()[:256 * 1024]
    return {"path": rel, "content": data.decode("utf-8", "replace"), "truncated": target.stat().st_size > len(data)}


def refresh(db: Session, wt: Worktree) -> Worktree:
    """Update modified files and cross-worktree overlaps (conflict tracking)."""
    path = Path(wt.path)
    wt.modified_files = g.changed_files(path, wt.base_commit)
    others = db.scalars(select(Worktree).where(Worktree.project_id == wt.project_id, Worktree.id != wt.id,
                                               Worktree.status.in_(["active", "committed"]))).all()
    overlaps = [{"worktree_id": o.id, "task_id": o.task_id, "files": sorted(set(o.modified_files) & set(wt.modified_files))}
                for o in others if set(o.modified_files) & set(wt.modified_files)]
    textual = g.merge_conflicts(Path(wt.path).parent.parent / "repo", wt.base_branch, wt.branch) \
        if wt.head_commit else []
    wt.conflicts = [*overlaps, *([{"with": wt.base_branch, "files": textual}] if textual else [])]
    return wt


def commit(db: Session, wt: Worktree, agent: Agent, message: str, protected: list[str]) -> dict[str, Any]:
    path = Path(wt.path)
    branch = g.current_branch(path)
    g.assert_not_protected(branch, protected)  # GH-04, even if someone re-pointed the worktree
    if branch != wt.branch:
        raise g.GitError(f"worktree is on {branch!r}, expected {wt.branch!r}")
    g.git(path, "add", "-A")
    if not g.git(path, "status", "--porcelain"):
        return {"committed": False, "reason": "nothing to commit"}
    msg = (message or "").strip()[:2000] or f"Task {wt.task_id}"
    g.git(path, "commit", "-q", "-m", msg, author=(agent.name, f"{agent.identifier}@agents.acc.local"))
    wt.head_commit, wt.status = g.head(path), "committed"
    wt.commits = [*wt.commits, {"sha": wt.head_commit, "message": msg[:200], "at": datetime.now(UTC).isoformat()}]
    refresh(db, wt)
    return {"committed": True, "sha": wt.head_commit, "files": wt.modified_files, "conflicts": wt.conflicts}


# ---------------- approved merge (GH-06) ----------------
def merge_approved(db: Session, a: Approval) -> dict[str, Any]:
    """Approval Center executor for `git.merge_protected`: merge exactly the approved commit, or refuse."""
    wt = db.get(Worktree, int(a.payload["worktree_id"]))
    if wt is None or wt.organization_id != a.organization_id:
        raise g.GitError("worktree missing")
    repo = Path(wt.path).parent.parent / "repo"
    tip = g.git(repo, "rev-parse", wt.branch)
    if tip != a.payload["head_commit"]:
        raise g.GitError("branch changed after approval; request a new approval")
    if g.current_branch(repo) != a.payload["base_branch"]:
        raise g.GitError("project repository is not on the base branch")
    conflicts = g.merge_conflicts(repo, a.payload["base_branch"], wt.branch)
    if conflicts:
        raise g.GitError(f"merge conflicts: {', '.join(conflicts)}")
    g.git(repo, "merge", "--no-ff", "-q", "-m", f"Merge {wt.branch} (approval #{a.id})", tip, author=g.PLATFORM)
    wt.status, wt.merged_at = "merged", datetime.now(UTC)
    runner.ensure_workspace(wt.organization_id, wt.project_id)
    return {"merged": tip, "into": a.payload["base_branch"], "merge_commit": g.head(repo)}


def _register() -> None:
    from fastapi import HTTPException

    from app.governance.approvals import register_executor

    def executor(db: Session, a: Approval) -> dict[str, Any]:
        try:
            return merge_approved(db, a)
        except g.GitError as e:  # surfaced by the Approval Center as execution_failed evidence
            raise HTTPException(409, str(e)) from e

    register_executor("git.merge_protected", executor)


_register()


def worktree_for_execution(db: Session, ex: Execution) -> Worktree | None:
    if ex.task_id is None:
        return None
    task = db.get(Task, ex.task_id)
    agent = db.get(Agent, ex.agent_id) if ex.agent_id else None
    if task is None or agent is None or task.project_id is None:
        return None
    return ensure_worktree(db, task, agent)
