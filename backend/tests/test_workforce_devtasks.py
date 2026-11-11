"""WF-14 — development tasks from text workers: file blocks applied to the task's branch by the platform, tests run in
the Docker sandbox, conflicts tracked, and the merge into the base branch only through the Approval Center.

Tier: mock + local integration. The Manager is the mock provider (labelled, not AI). Workers are Human-Assisted: the
"result" below is what a person would paste from an Arena session. Git is real; the tests run in the real sandbox
container when Docker and the sandbox image are present (otherwise those tests are skipped and say why).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from app.devplane import git as g
from app.devplane import worktrees
from app.models import Approval, ManagerStep, Project, Worktree
from app.workforce import devtasks, report
from app.workforce.review import dev_outcome
from tests import wf as h
from tests.test_sandbox import needs_docker

API = "/api/workforce"
PYTEST = ["python3", "-m", "pytest", "-q"]
ONE = "Build:\n1. Add the calculator [engineering] #code <README.md> @W2"

GOOD = """Here is the calculator.

```file:calc.py
def add(a, b):
    return a + b
```

```file:test_calc.py
from calc import add


def test_add():
    assert add(2, 3) == 5
```
"""
BAD = GOOD.replace("return a + b", "return a - b")


@pytest.fixture()
def force(db):
    """The default workforce, development off, and a fresh repository for its project (every test starts from the
    repository's initial commit)."""
    import shutil

    from app.sandbox import runner
    f = h.reset(db)
    f.settings = {}
    for wt in db.scalars(select(Worktree).where(Worktree.project_id == f.project_id)):
        db.delete(wt)
    db.commit()
    shutil.rmtree(runner.project_root(f.organization_id, f.project_id), ignore_errors=True)
    return f


def enable(admin_client, command=PYTEST, **extra) -> dict:
    r = admin_client.patch(f"{API}/development", json={"enabled": True, "test_command": command, **extra})
    assert r.status_code == 200, r.text
    return r.json()["development"]


def submit(client, task_id: int, content: str) -> dict:
    assert client.post(f"{API}/tasks/{task_id}/accept").status_code == 200
    r = client.post(f"{API}/tasks/{task_id}/submit", json={"content": content})
    assert r.status_code == 201, r.text
    return r.json()


def started(db, admin_client):
    wf = h.planned(db, ONE)
    h.tick(wf)
    (t,) = h.tasks_of(db, wf).values()
    return wf, t


def repo_of(db, t) -> Path:
    return worktrees.repo_path(t.organization_id, db.get(Project, t.project_id).id)


# ---------------- parsing ----------------
def test_file_blocks_are_parsed_with_deletes_long_fences_and_last_block_wins():
    text = ("intro\n```file:a.py\nx = 1\n```\n"
            "````file:docs/readme.md\n# Title\n```python\nprint(1)\n```\n````\n"
            "```delete:old.py\n```\n```file:./a.py\nx = 2\n```\n")
    changes = {c.path: c.content for c in devtasks.parse_blocks(text)}
    assert changes == {"a.py": "x = 2\n", "docs/readme.md": "# Title\n```python\nprint(1)\n```\n", "old.py": None}
    with pytest.raises(ValueError, match="no_file_blocks"):
        devtasks.parse_blocks("I changed calc.py so that it adds.")


def test_a_failed_platform_check_is_a_revision_until_the_limit_then_a_person():
    assert dev_outcome({"code": "tests_failed"}, used=0, limit=2)["outcome"] == "revise"
    assert dev_outcome({"code": "tests_failed"}, used=2, limit=2) == {
        "outcome": "needs_human", "reason": "revision_limit_reached", "overridden": "dev_check_failed",
        "dev_code": "tests_failed"}
    assert dev_outcome({"code": "development_disabled", "needs_human": True}, used=0, limit=2)["outcome"] == \
        "needs_human"


# ---------------- configuration ----------------
def test_settings_need_manage_and_an_allow_listed_test_command(admin_client, db, force, make_user):
    assert admin_client.get(API).json()["workforce"]["development"]["enabled"] is False
    r = admin_client.patch(f"{API}/development", json={"enabled": True, "test_command": ["bash", "-c", "rm -rf /"]})
    assert r.status_code == 422 and r.json()["detail"].startswith("test_command_not_allowed")
    r = admin_client.patch(f"{API}/development", json={"test_command": ["pytest", "--rootdir=/"]})
    assert r.status_code == 422
    dev = enable(admin_client, timeout_seconds=120)
    assert dev["enabled"] is True and dev["test_command"] == PYTEST and dev["timeout_seconds"] == 120
    assert "python3 -m pytest" in dev["allowed_test_commands"]
    # a person without workforce.manage cannot change what the platform executes
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import login_as
    make_user("wf_dev_viewer", "VIEWER")
    viewer = login_as(TestClient(app), "wf_dev_viewer", "user-password-123")
    assert viewer.patch(f"{API}/development", json={"enabled": False}).status_code == 403


def test_the_manager_cannot_plan_code_while_development_is_off(db, force):
    wf = h.objective(db, ONE)
    from app.workforce import control
    assert asyncio.run(control.plan(wf.id)) == "failed"
    db.expire_all()
    assert "development tasks are not enabled" in (db.get(type(wf), wf.id).error or "")


# ---------------- the brief ----------------
def test_the_worker_is_told_the_format_and_shown_the_repository(admin_client, db, force):
    enable(admin_client)
    _wf, t = started(db, admin_client)
    assert t.result["kind"] == "code" and t.result["files"] == ["README.md"]
    card = admin_client.get(f"{API}/tasks/{t.id}").json()
    assert card["kind"] == "code" and card["development"]["files_shown"] == ["README.md"]
    brief = card["brief"]["message"]
    assert "DEVELOPMENT TASK — HOW TO DELIVER" in brief and "```file:" in brief and "python3 -m pytest" in brief
    assert "- README.md" in brief and "<<<REPOSITORY FILE README.md" in brief and "# Project" in brief
    # stable: shown twice, the same message and hash (it is tied to the submission)
    assert admin_client.get(f"{API}/tasks/{t.id}").json()["brief"]["sha256"] == card["brief"]["sha256"]


# ---------------- end to end ----------------
@needs_docker
def test_code_is_applied_tested_reviewed_and_merged_only_after_a_person_approves(admin_client, db, force,
                                                                                 make_user):
    enable(admin_client)
    wf, t = started(db, admin_client)
    submit(admin_client, t.id, GOOD)
    out = h.tick(wf)
    assert list(out["reviewed"].values()) == ["approve"]
    t = h.task(db, t.id)
    assert t.lifecycle == "APPROVED"  # accepted by the Manager, but nothing merged yet
    (sub,) = h.subs(db, t)
    dev = sub.review["dev"]
    assert dev["ok"] and dev["code"] == "tests_passed" and dev["tests"]["passed"] and dev["tests"]["exit_code"] == 0
    assert {f["path"] for f in dev["files"]} == {"calc.py", "test_calc.py"}
    assert {c["check"]: c["passed"] for c in sub.review["checks"]} == {
        "result_present": True, "files_applied": True, "tests": True}
    ver = [e for e in t.evidence if e["kind"] == "verification"]
    assert ver and ver[-1]["passed"] and ver[-1]["submission_id"] == sub.id
    assert ver[-1]["head_commit"] == dev["head_commit"]

    wt = db.get(Worktree, dev["worktree_id"])
    assert wt.branch == dev["branch"] and wt.branch.startswith("agent/") and wt.branch.endswith(f"task-{t.id}")
    assert sorted(wt.modified_files) == ["calc.py", "test_calc.py"]
    repo = repo_of(db, t)
    assert not (repo / "calc.py").exists()  # the base branch is untouched until the approval

    ap = db.get(Approval, t.result["approval_id"])
    assert ap.operation == "git.merge_protected" and ap.status == "pending"
    assert ap.payload["head_commit"] == dev["head_commit"] and ap.payload["content_sha256"] == sub.content_sha256
    assert ap.payload["worktree_id"] == wt.id and ap.payload["base_branch"] == "main"
    # the submitter cannot approve their own merge
    assert admin_client.post(f"/api/approvals/{ap.id}/decide", json={"decision": "approve", "reason": ""}
                             ).status_code == 403
    second = h.second_person(make_user)
    r = second.post(f"/api/approvals/{ap.id}/decide", json={"decision": "approve", "reason": "looks right"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Approval, ap.id).status == "executed"
    t = h.task(db, t.id)
    assert t.lifecycle == "COMPLETED" and t.result["merged"]["into"] == "main"
    assert (repo / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert g.git(repo, "log", "-1", "--format=%s").startswith(f"Merge {wt.branch} (approval #{ap.id})")

    h.drive(db, wf)
    rep = asyncio.run(report.consolidate(db, wf.id))
    (item,) = rep.content["items"]
    assert item["class"] == "verified" and item["basis"]["verification"] == ["tests_passed"]
    assert rep.content["counts"]["verified"] == 1
    assert not any("did not check the content" in f for f in rep.content["facts"])


@needs_docker
def test_failing_tests_send_the_result_back_with_the_output_and_no_model_is_asked(admin_client, db, force):
    enable(admin_client)
    wf, t = started(db, admin_client)
    submit(admin_client, t.id, BAD)
    out = h.tick(wf)
    assert list(out["reviewed"].values()) == ["revise"]
    t = h.task(db, t.id)
    (sub,) = h.subs(db, t)
    assert t.lifecycle in ("REVISION_REQUESTED", "WAITING_FOR_ACCEPTANCE")
    assert sub.decision == "revision_requested" and sub.review["dev"]["code"] == "tests_failed"
    assert sub.review["overridden"] == "dev_check_failed" and sub.review["verdict"] is None
    note = t.result["review_notes"][-1]
    assert "tests" in note and "exited with code" in note and "assert" in note  # the platform's own output
    assert not db.scalars(select(ManagerStep).where(ManagerStep.submission_id == sub.id)).all()
    assert [e for e in t.evidence if e["kind"] == "verification"][-1]["passed"] is False
    # the next version is applied to a clean base: it passes and goes on
    h.tick(wf)
    t = h.task(db, t.id)
    if t.lifecycle == "WAITING_FOR_ACCEPTANCE":
        submit(admin_client, t.id, GOOD)
    out = h.tick(wf)
    t = h.task(db, t.id)
    assert t.lifecycle == "APPROVED"
    assert h.subs(db, t)[-1].review["dev"]["code"] == "tests_passed"


def test_results_without_blocks_or_with_unsafe_paths_are_not_applied(admin_client, db, force):
    enable(admin_client, command=None)
    wf, t = started(db, admin_client)
    submit(admin_client, t.id, "I added calc.py with an add function; the tests pass.")
    h.tick(wf)
    t = h.task(db, t.id)
    (sub,) = h.subs(db, t)
    assert sub.review["dev"]["code"] == "no_file_blocks" and sub.decision == "revision_requested"
    assert "```file:" in t.result["review_notes"][-1]
    h.tick(wf)
    t = h.task(db, t.id)
    submit(admin_client, t.id, "```file:../escape.py\nprint('out')\n```\n")
    h.tick(wf)
    t = h.task(db, t.id)
    last = h.subs(db, t)[-1]
    assert last.review["dev"]["code"] == "apply_failed" and "outside the worktree" in last.review["dev"]["feedback"]
    assert not (repo_of(db, t).parent / "escape.py").exists()
    assert not (repo_of(db, t).parent.parent / "escape.py").exists()


def test_without_a_test_command_the_code_is_applied_but_never_classified_verified(admin_client, db, force,
                                                                                  make_user):
    enable(admin_client, command=None)
    wf, t = started(db, admin_client)
    submit(admin_client, t.id, GOOD)
    h.tick(wf)
    t = h.task(db, t.id)
    (sub,) = h.subs(db, t)
    assert t.lifecycle == "APPROVED" and sub.review["dev"]["code"] == "tests_not_configured"
    assert not [e for e in t.evidence if e["kind"] == "verification"]
    ap = db.get(Approval, t.result["approval_id"])
    assert ap.operation == "git.merge_protected"
    second = h.second_person(make_user)
    assert second.post(f"/api/approvals/{ap.id}/decide", json={"decision": "approve", "reason": ""}
                       ).status_code == 200
    h.drive(db, wf)
    rep = asyncio.run(report.consolidate(db, wf.id))
    assert rep.content["items"][0]["class"] == "human_approved"


def test_a_merge_that_fails_after_approval_goes_back_for_revision(admin_client, db, force, make_user):
    enable(admin_client, command=None)
    wf, t = started(db, admin_client)
    submit(admin_client, t.id, GOOD)
    h.tick(wf)
    t = h.task(db, t.id)
    ap = db.get(Approval, t.result["approval_id"])
    # meanwhile someone changed the base branch so that the branch no longer merges cleanly
    repo = repo_of(db, t)
    (repo / "calc.py").write_text("def add(a, b):\n    return b + a + 0\n")
    g.git(repo, "add", "calc.py")
    g.git(repo, "commit", "-q", "-m", "conflicting change", author=g.PLATFORM)
    second = h.second_person(make_user)
    assert second.post(f"/api/approvals/{ap.id}/decide", json={"decision": "approve", "reason": ""}
                       ).status_code == 200
    db.expire_all()
    assert db.get(Approval, ap.id).status == "approved"  # decided, but the merge refused to run
    t = h.task(db, t.id)
    assert t.lifecycle in ("REVISION_REQUESTED", "WAITING_FOR_ACCEPTANCE")
    assert t.result["review_notes"][-1].startswith("MERGE FAILED")
    assert (repo / "calc.py").read_text().startswith("def add(a, b):\n    return b + a")
    # the next delivery is applied on top of the new base branch, and the conflict is gone
    h.tick(wf)
    t = h.task(db, t.id)
    submit(admin_client, t.id, GOOD)
    h.tick(wf)
    t = h.task(db, t.id)
    dev = h.subs(db, t)[-1].review["dev"]
    assert dev["ok"] and dev["base_commit"] == g.git(repo, "rev-parse", "main")


def test_a_conflict_with_a_moved_base_branch_is_tracked_on_the_worktree(admin_client, db, force):
    enable(admin_client, command=None)
    wf, t = started(db, admin_client)
    submit(admin_client, t.id, GOOD)
    h.tick(wf)
    t = h.task(db, t.id)
    wt = db.get(Worktree, h.subs(db, t)[-1].review["dev"]["worktree_id"])
    # base branch moves after the worktree was created; the worktree stays on its old commit
    repo = repo_of(db, t)
    (repo / "calc.py").write_text("x = 'base'\n")
    g.git(repo, "add", "calc.py")
    g.git(repo, "commit", "-q", "-m", "base change", author=g.PLATFORM)
    # a refresh of the existing commit now reports the textual conflict
    worktrees.refresh(db, wt)
    assert any(c.get("with") == "main" and "calc.py" in c["files"] for c in wt.conflicts)
    db.rollback()
