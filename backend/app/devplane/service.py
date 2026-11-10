"""GitHub integration services (GH-01, GH-05, GH-06 remote, GH-07, ADR-022)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.devplane import git as g
from app.devplane.github import GitHub, GitHubError
from app.models import (
    ApiCredential,
    Approval,
    EvaluationResult,
    Execution,
    ExecutionEvent,
    ProcessedMessage,
    Project,
    PullRequest,
    Repository,
    Task,
    Worktree,
)
from app.security.crypto import decrypt_secret, redact

REMOTE_BASE = "https://github.com"  # tests point this at a local bare repository


def client_for(db: Session, repo: Repository) -> GitHub:
    cred = db.get(ApiCredential, repo.credential_id) if repo.credential_id else None
    if cred is None:
        raise GitHubError(400, "repository has no GitHub credential")
    return GitHub(decrypt_secret(cred.encrypted_value), api_base=repo.api_base)


def remote_url(repo: Repository) -> str:
    return f"{REMOTE_BASE}/{repo.full_name}.git"


def run_sync(coro):
    """Run a coroutine from synchronous code (Approval Center executors run in a worker thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("run_sync called inside an event loop")


# ---------------- GH-05: pull request description ----------------
def pr_description(db: Session, task: Task, wt: Worktree) -> str:
    ev = db.scalar(select(EvaluationResult).where(EvaluationResult.task_id == task.id)
                   .order_by(EvaluationResult.id.desc()).limit(1))
    checks = next((e["checks"] for e in (ev.evidence if ev else []) if e.get("kind") == "checks"), [])
    crit = {c.get("criterion"): c.get("passed") for c in checks if c.get("check") == "acceptance_criterion"}
    tests = db.scalar(select(ExecutionEvent).join(Execution, Execution.id == ExecutionEvent.execution_id).where(
        Execution.task_id == task.id, ExecutionEvent.type == "tool_result",
        ExecutionEvent.payload["tool"].astext == "shell.test").order_by(ExecutionEvent.id.desc()).limit(1))
    is_mock = db.scalar(select(Execution.is_mock).where(Execution.task_id == task.id).order_by(
        Execution.created_at.desc()).limit(1))
    lines = [f"## {task.title}", "", (task.description or "").strip(), "", "### Acceptance criteria"]
    lines += [f"- [{'x' if crit.get(c) else ' '}] {c}" for c in task.acceptance_criteria or []] or ["- (none)"]
    lines += ["", "### Tests (platform sandbox)"]
    if tests is not None:
        r = tests.payload.get("result", {})
        summary = (r.get("stdout") or "").strip().splitlines()[-1:] or [""]
        timed = " (timed out)" if r.get("timed_out") else ""
        lines += [f"- Sandbox test run → exit {r.get('exit_code')}{timed}: {summary[0][:200]}"]
    else:
        lines += ["- No sandbox test run was recorded for this task."]
    lines += ["", "### Evaluation", f"- Decision: **{ev.decision}**" if ev else "- Not evaluated yet."]
    if ev is not None and ev.overall is not None:
        lines += [f"- Overall: {round(ev.overall * 100)}%"]
    lines += ["", "### Changes", *[f"- `{f}`" for f in wt.modified_files[:100]]]
    if wt.conflicts:
        lines += ["", "### ⚠ Overlaps / conflicts", *[f"- {json.dumps(c)}" for c in wt.conflicts]]
    if is_mock:
        lines += ["", "> ⚠ Produced with the **mock** provider — not real AI output."]
    lines += ["", f"_Opened by AI Agent Command Center for task #{task.id}. Merging requires a human approval._"]
    return redact("\n".join(lines))[:60_000]


async def open_pull_request(db: Session, task: Task, wt: Worktree) -> PullRequest:
    project = db.get(Project, task.project_id)
    repo = db.get(Repository, project.repository_id) if project.repository_id else None
    if repo is None:
        raise GitHubError(400, "the project has no GitHub repository")
    existing = db.scalar(select(PullRequest).where(PullRequest.task_id == task.id, PullRequest.state == "open"))
    from app.devplane.worktrees import protected_branches
    cred = db.get(ApiCredential, repo.credential_id) if repo.credential_id else None
    g.push_branch(Path(wt.path), remote_url(repo), wt.branch, decrypt_secret(cred.encrypted_value) if cred else None,
                  protected_branches(project))
    if existing is not None:
        return existing
    gh = client_for(db, repo)
    try:
        pr = await gh.create_pull(repo.full_name, title=f"[task #{task.id}] {task.title}"[:250], head=wt.branch,
                                  base=wt.base_branch, body=pr_description(db, task, wt))
    finally:
        await gh.aclose()
    row = PullRequest(organization_id=task.organization_id, repository_id=repo.id, task_id=task.id,
                      number=int(pr["number"]), url=str(pr.get("html_url", "")), title=str(pr.get("title", ""))[:255],
                      head_branch=wt.branch, base_branch=wt.base_branch, state="open")
    db.add(row)
    db.flush()
    return row


# ---------------- GH-06 (remote): merge through GitHub ----------------
def merge_pull_approved(db: Session, a: Approval) -> dict[str, Any]:
    pr = db.get(PullRequest, int(a.payload["pull_request_id"]))
    repo = db.get(Repository, pr.repository_id)
    gh = client_for(db, repo)

    async def go():
        try:
            return await gh.merge_pull(repo.full_name, pr.number, sha=a.payload["head_commit"],
                                       title=f"Merge #{pr.number} (approval #{a.id})")
        finally:
            await gh.aclose()
    try:
        res = run_sync(go())
    except GitHubError as e:
        raise g.GitError(f"GitHub refused the merge: {e.message}") from e
    pr.state = "merged"
    return {"merged": bool(res.get("merged")), "sha": res.get("sha"), "pull": pr.number}


# ---------------- GH-07: webhooks ----------------
def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def first_delivery(db: Session, delivery_id: str) -> bool:
    """Replay protection: each X-GitHub-Delivery is processed once."""
    inserted = db.scalar(insert(ProcessedMessage).values(consumer="github.webhook", idempotency_key=delivery_id[:160],
                                                         message_id=delivery_id[:36], outcome="done")
                         .on_conflict_do_nothing(constraint="uq_processed_messages_consumer")
                         .returning(ProcessedMessage.id))
    return inserted is not None


def apply_event(db: Session, repo: Repository, event: str, payload: dict[str, Any]) -> str:
    if event == "ping":
        return "pong"
    if event == "pull_request":
        pr_data = payload.get("pull_request") or {}
        pr = db.scalar(select(PullRequest).where(PullRequest.repository_id == repo.id,
                                                 PullRequest.number == int(pr_data.get("number", -1))))
        if pr is None:
            return "unknown_pull_request"
        if pr_data.get("merged"):
            pr.state = "merged"
        elif pr_data.get("state") in ("open", "closed"):
            pr.state = pr_data["state"]
        return f"pull_request:{pr.state}"
    if event in ("check_run", "status"):
        if event == "check_run":
            cr = payload.get("check_run") or {}
            sha, name = cr.get("head_sha"), cr.get("name")
            result = {"status": cr.get("status"), "conclusion": cr.get("conclusion")}
        else:
            sha, name = payload.get("sha"), payload.get("context")
            result = {"status": "completed" if payload.get("state") != "pending" else "in_progress",
                      "conclusion": payload.get("state")}
        if not sha or not name:
            return "ignored"
        wt = db.scalar(select(Worktree).where(Worktree.project_id.in_(
            select(Project.id).where(Project.repository_id == repo.id)), Worktree.head_commit == sha))
        prs = db.scalars(select(PullRequest).where(PullRequest.repository_id == repo.id,
                                                   PullRequest.head_branch == (wt.branch if wt else None))).all()
        for pr in prs:
            pr.checks = {**(pr.checks or {}), str(name)[:100]: {**result, "sha": sha}}
        return f"{event}:{len(prs)}"
    return "ignored"
