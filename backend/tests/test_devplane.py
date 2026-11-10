"""M8 — GH-03 worktrees, GH-04 protected branches, GH-06 merge only via approval. Real git, real files."""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

import app.devplane.tools
import app.main  # noqa: F401
from app.devplane import git as g
from app.devplane import worktrees as w
from app.governance import approvals
from app.models import (
    Agent,
    Approval,
    AuditLog,
    EvaluationResult,
    Execution,
    ExecutionEvent,
    Project,
    Repository,
    Task,
    Worktree,
)
from app.orchestration import scheduler
from app.runtime import contract, service
from app.runtime.engines.mock import MockEngine

TOOLS = ["repo.read", "files.write", "git.branch", "git.commit"]


@pytest.fixture(autouse=True)
def mock_engine(monkeypatch):
    monkeypatch.setitem(contract._ENGINES, "api", MockEngine)


def _agent(db, tools=TOOLS) -> Agent:
    a = Agent(organization_id=1, name=f"Dev {uuid.uuid4().hex[:4]}", identifier=f"dev-{uuid.uuid4().hex[:6]}",
              role="backend", state="IDLE", execution_engine="api", security_profile="developer",
              tool_permissions=list(tools), concurrency_limit=5,
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1})
    db.add(a)
    db.commit()
    return a


def _project(db, **kw) -> Project:
    p = Project(organization_id=1, name=f"Dev {uuid.uuid4().hex[:6]}", **kw)
    db.add(p)
    db.commit()
    return p


def _task(db, project, agent, title="t") -> Task:
    t = Task(organization_id=1, project_id=project.id, title=title, status="IN_PROGRESS", assigned_agent_id=agent.id,
             acceptance_criteria=["done"], attempts=1)
    db.add(t)
    db.commit()
    return t


def _run_tools(db, agent, task, steps) -> list[dict]:
    ex = service.create_execution(db, organization_id=1, agent=agent, project_id=task.project_id, task_id=task.id,
                                  actor="test", input={"mock_script": steps})
    db.commit()
    asyncio.run(service.run_execution(ex.id))
    db.expire_all()
    return [e.payload["result"] for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == ex.id, ExecutionEvent.type == "tool_result").order_by(ExecutionEvent.seq))]


def test_repository_hooks_and_config_never_execute(tmp_path):
    repo = tmp_path / "r"
    g.init_repo(repo)
    marker = tmp_path / "pwned"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    g.git(repo, "config", "core.hooksPath", str(repo / ".git" / "hooks"))  # repo tries to re-enable its hooks
    (repo / "a.txt").write_text("x")
    g.git(repo, "add", "a.txt")
    g.git(repo, "commit", "-q", "-m", "m", author=("A", "a@x"))
    assert not marker.exists()


def test_each_task_gets_an_isolated_worktree_and_overlaps_are_tracked(db):
    p, a1, a2 = _project(db), _agent(db), _agent(db)
    t1, t2 = _task(db, p, a1, "one"), _task(db, p, a2, "two")
    r1 = _run_tools(db, a1, t1, [{"tool": "files.write", "args": {"path": "src/app.py", "content": "A = 1\n"}},
                                 {"tool": "files.write", "args": {"path": "shared.txt", "content": "from one\n"}},
                                 {"tool": "git.commit", "args": {"message": "feat: one"}}])
    r2 = _run_tools(db, a2, t2, [{"tool": "repo.read", "args": {"path": "."}},
                                 {"tool": "files.write", "args": {"path": "shared.txt", "content": "from two\n"}},
                                 {"tool": "git.commit", "args": {"message": "feat: two"}}])
    assert r1[2]["committed"] and r2[2]["committed"]
    assert "src/" not in r2[0]["entries"]  # task two does not see task one's uncommitted/branch work
    wt1, wt2 = (db.scalar(select(Worktree).where(Worktree.task_id == t.id)) for t in (t1, t2))
    assert wt1.branch == f"agent/{a1.identifier}/task-{t1.id}" and wt1.path != wt2.path
    assert wt1.modified_files == ["shared.txt", "src/app.py"] and wt2.modified_files == ["shared.txt"]
    db.refresh(wt1)
    w.refresh(db, wt2)
    assert {"worktree_id": wt1.id, "task_id": t1.id, "files": ["shared.txt"]} in wt2.conflicts
    repo = Path(wt1.path).parent.parent / "repo"
    assert g.current_branch(repo) == "main" and not (repo / "src" / "app.py").exists()  # main untouched
    log = g.git(Path(wt1.path), "log", "-1", "--format=%an <%ae>|%cn")
    assert log == f"{a1.name} <{a1.identifier}@agents.acc.local>|AI Agent Command Center"


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", ".git/config", "a/../../x", ""])
def test_file_tool_refuses_paths_outside_the_worktree(db, path):
    p, a = _project(db), _agent(db)
    t = _task(db, p, a)
    res = _run_tools(db, a, t, [{"tool": "files.write", "args": {"path": path, "content": "x"}}])
    assert res[0]["error"] == "refused"


def test_symlink_escape_refused(db, tmp_path):
    p, a = _project(db), _agent(db)
    t = _task(db, p, a)
    _run_tools(db, a, t, [{"tool": "git.branch", "args": {}}])
    wt = db.scalar(select(Worktree).where(Worktree.task_id == t.id))
    (Path(wt.path) / "link").symlink_to(tmp_path)
    res = _run_tools(db, a, t, [{"tool": "files.write", "args": {"path": "link/x.txt", "content": "x"}},
                                {"tool": "files.write", "args": {"path": "link", "content": "x"}}])
    assert res[0]["error"] == "refused" and res[1]["error"] == "refused"
    assert not (tmp_path / "x.txt").exists()


def test_protected_branches_cannot_be_written_by_agents(db):
    a = _agent(db)
    p = _project(db)
    t = _task(db, p, a)
    repo = Repository(organization_id=1, full_name=f"acme/{uuid.uuid4().hex[:6]}",
                      protected_branches=["main", f"agent/{a.identifier}/task-{t.id}"])
    db.add(repo)
    db.flush()
    p.repository_id = repo.id
    db.commit()
    res = _run_tools(db, a, t, [{"tool": "files.write", "args": {"path": "x", "content": "x"}}])
    assert res[0]["error"] == "protected_branch"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "devplane.protected_branch_blocked",
                                            AuditLog.agent_id == a.id))
    # a worktree moved off its branch (detached / onto another branch) cannot commit either
    p2, t2 = _project(db), None
    t2 = _task(db, p2, a)
    _run_tools(db, a, t2, [{"tool": "files.write", "args": {"path": "y", "content": "y"}}])
    wt = db.scalar(select(Worktree).where(Worktree.task_id == t2.id))
    g.git(Path(wt.path), "checkout", "-q", "--detach")
    res = _run_tools(db, a, t2, [{"tool": "git.commit", "args": {"message": "sneaky"}}])
    assert res[0]["error"] == "protected_branch"


def _reviewable(db, p, a, files: dict[str, str]) -> tuple[Task, Worktree]:
    t = _task(db, p, a, "feature")
    steps = [{"tool": "files.write", "args": {"path": k, "content": v}} for k, v in files.items()]
    _run_tools(db, a, t, [*steps, {"tool": "git.commit", "args": {"message": "feat"}}])
    ev = EvaluationResult(organization_id=1, task_id=t.id, agent_id=a.id, decision="approve", overall=1.0, scores={})
    db.add(ev)
    db.flush()
    t.status, t.result = "REVIEW", {"evaluation_id": ev.id, "output": "done"}
    db.commit()
    return t, db.scalar(select(Worktree).where(Worktree.task_id == t.id))


def test_merge_happens_only_through_an_approved_request(db, make_user):
    p, a = _project(db), _agent(db)
    t, wt = _reviewable(db, p, a, {"feature.py": "def f():\n    return 1\n"})
    repo = Path(wt.path).parent.parent / "repo"
    scheduler.review(db, t, "approve", "looks good", "user:1")
    db.refresh(t)
    assert t.status == "APPROVAL"
    ap = db.get(Approval, t.result["approval_id"])
    assert ap.operation == "git.merge_protected" and ap.payload["head_commit"] == wt.head_commit
    assert not (repo / "feature.py").exists()  # nothing merged while pending
    with pytest.raises(Exception, match="approval_not_approved"):
        approvals.execute(db, ap)  # no way around the decision
    approvals.decide(db, ap, user_id=make_user("merger", "ORGANIZATION_ADMIN").id, decision="approve", reason="")
    db.commit()
    db.refresh(ap)
    db.refresh(t)
    assert ap.status == "executed" and t.status == "COMPLETED"
    assert (repo / "feature.py").read_text().startswith("def f()") and g.current_branch(repo) == "main"
    assert "approval #" in g.git(repo, "log", "-1", "--format=%s")
    assert db.get(Worktree, wt.id).status == "merged"


def test_branch_moved_after_approval_request_is_not_merged(db, make_user):
    p, a = _project(db), _agent(db)
    t, wt = _reviewable(db, p, a, {"a.py": "A = 1\n"})
    scheduler.review(db, t, "approve", "", "user:1")
    db.refresh(t)
    ap = db.get(Approval, t.result["approval_id"])
    (Path(wt.path) / "a.py").write_text("A = 666  # slipped in after review\n")
    g.git(Path(wt.path), "commit", "-qam", "late change", author=("x", "x@x"))
    approvals.decide(db, ap, user_id=make_user("merger2", "ORGANIZATION_ADMIN").id, decision="approve", reason="")
    db.commit()
    db.refresh(ap)
    db.refresh(t)
    assert ap.status == "approved" and ap.evidence[-1]["kind"] == "execution_failed"
    assert "branch changed after approval" in ap.evidence[-1]["error"]
    assert t.status == "READY" and "approved but not executed" in t.result["review_notes"][-1]
    repo = Path(wt.path).parent.parent / "repo"
    assert not (repo / "a.py").exists()


def test_textual_conflict_with_base_blocks_review_approval(db):
    p, a = _project(db), _agent(db)
    t, wt = _reviewable(db, p, a, {"README.md": "# Agent version\n"})
    repo = Path(wt.path).parent.parent / "repo"
    (repo / "README.md").write_text("# Someone else changed main\n")
    g.git(repo, "commit", "-qam", "main moved", author=("h", "h@x"))
    with pytest.raises(Exception, match="merge_conflicts:README.md"):
        scheduler.review(db, t, "approve", "", "user:1")


def test_sandbox_tests_run_inside_the_task_worktree(db):
    import shutil
    import subprocess

    from app.config import get_settings
    if not shutil.which("docker") or subprocess.run(["docker", "image", "inspect", get_settings().sandbox_image],
                                                    capture_output=True, check=False).returncode:
        pytest.skip("docker or sandbox image unavailable")
    p = _project(db)
    a = _agent(db, tools=[*TOOLS, "shell.test"])
    t = _task(db, p, a)
    res = _run_tools(db, a, t, [
        {"tool": "files.write", "args": {"path": "test_calc.py", "content": "def test_x():\n    assert 2 * 2 == 4\n"}},
        {"tool": "shell.test", "args": {"argv": ["python3", "-m", "pytest", "-q"]}}])
    assert res[1]["exit_code"] == 0 and "1 passed" in res[1]["stdout"]
    db.expire_all()
    assert db.scalar(select(Execution).where(Execution.task_id == t.id)).status == "COMPLETED"
