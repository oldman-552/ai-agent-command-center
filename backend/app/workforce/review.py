"""Review of a submitted result (WF-11): approve, request a revision, reject, or hand the decision to a person.

A result is never accepted because a request succeeded. Every submission — from a provider or from a person — is
reviewed against the task's acceptance criteria:

    1. deterministic checks by the platform (there is a result; it is the latest version)
    2. the Manager's verdict per criterion, as validated JSON
    3. the platform's rules on top of that verdict — the Manager cannot override them:
         · a criterion marked unmet can never be approved by the Manager
         · an approval of a result that contains instruction-like text goes to a person
         · revisions are limited; after the limit a person decides
         · with review policy `human`, the Manager's verdict is advice and a person decides
         · a task that performs a critical operation is completed only by a human approval in the Approval Center,
           for exactly the submission that was reviewed (its hash is frozen in the request)

Submissions are immutable: a revision is a new version, and every version keeps its own review.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.governance.approvals import on_decision, payload_hash, request_approval
from app.models import Approval, Task, TaskSubmission, User, Workflow, Workforce
from app.models import TaskLifecycle as L
from app.security.audit import log_activity
from app.security.crypto import contains_credential
from app.workforce import devtasks, manager, prompts
from app.workforce.lifecycle import move
from app.workforce.scheduler import KIND, latest_submission, reopen_if_needed, request_tick

log = logging.getLogger("workforce.review")
MAX_CONTENT_CHARS = 2_000_000


class ReviewError(ValueError):
    """A review action that is not possible in the task's current state (the API turns it into 409)."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def quote_in(evidence: str, content: str) -> bool:
    """Is the Manager's evidence really a quote from the submission? Checked by the platform, not by a model."""
    q = _norm(evidence)
    return len(q) >= 8 and q in _norm(content)


def revisions_used(db: Session, task_id: int) -> int:
    return db.scalar(select(func.count()).select_from(TaskSubmission).where(
        TaskSubmission.task_id == task_id, TaskSubmission.decision == "revision_requested")) or 0


def policy_of(db: Session, wf: Workflow) -> tuple[str, int]:
    force = db.get(Workforce, wf.workforce_id) if wf.workforce_id else None
    return (wf.review_policy or (force.review_policy if force else "human"),
            force.max_revisions if force else 0)


def outcome_of(verdict: prompts.Verdict | None, *, content_ok: bool, flagged: bool, policy: str, used: int,
               limit: int, problem: str | None = None) -> dict[str, Any]:
    """The platform's decision from the Manager's verdict. Pure, so every rule can be tested without a model."""
    if problem:  # no usable verdict
        return {"outcome": "needs_human", "reason": problem}
    assert verdict is not None
    unmet = sorted(c.index for c in verdict.criteria if not c.met)
    base: dict[str, Any] = {"advisory": verdict.decision, "unmet": unmet}
    if policy == "human":
        return {**base, "outcome": "needs_human", "reason": "review_policy_human"}
    revise_or_person = ({"outcome": "revise"} if used < limit
                        else {"outcome": "needs_human", "reason": "revision_limit_reached"})
    if not content_ok:
        return {**base, **revise_or_person, "overridden": "empty_result"}
    if verdict.decision == "approve":
        if unmet:
            return {**base, **revise_or_person, "overridden": "approve_with_unmet_criteria"}
        if flagged:
            return {**base, "outcome": "needs_human", "reason": "result_contains_instruction_like_text"}
        return {**base, "outcome": "approve"}
    if verdict.decision == "revise":
        return {**base, **revise_or_person}
    if verdict.decision == "reject":
        return {**base, "outcome": "reject"}
    return {**base, "outcome": "needs_human", "reason": "manager_escalated"}


def dev_outcome(dev: dict[str, Any], *, used: int, limit: int) -> dict[str, Any]:
    """The decision when the platform's own development check failed. Pure."""
    if dev.get("needs_human"):
        return {"outcome": "needs_human", "reason": f"development:{dev['code']}", "dev_code": dev["code"]}
    if used < limit:
        return {"outcome": "revise", "overridden": "dev_check_failed", "dev_code": dev["code"]}
    return {"outcome": "needs_human", "reason": "revision_limit_reached", "overridden": "dev_check_failed",
            "dev_code": dev["code"]}


async def _verdict(db: Session, wf: Workflow, task: Task, sub: TaskSubmission) -> tuple[prompts.Verdict | None,
                                                                                         dict[str, Any], str | None]:
    """Ask the Manager, validating its answer. Returns (verdict, manager info, problem)."""
    criteria = [str(c) for c in (task.acceptance_criteria or [])]
    nonce = prompts.new_nonce()
    error: str | None = None
    info: dict[str, Any] = {}
    for attempt in range(1, prompts.MAX_ATTEMPTS + 1):
        try:
            call = await manager.ask(
                db, wf, "review", prompts.REVIEW_SYSTEM,
                prompts.review_user(title=task.title, objective=task.objective or "",
                                    instructions=task.instructions or task.description or "",
                                    expected_output=task.expected_output or "", criteria=criteria,
                                    submission=sub.content, source=sub.source, attachments=sub.attachments or [],
                                    version=sub.version, nonce=nonce, error=error),
                task_id=task.id, submission_id=sub.id, attempt=attempt, max_tokens=4000)
        except manager.ManagerFailed as e:
            return None, info, f"manager_unavailable:{e.code}"
        except manager.ManagerUnavailable:
            return None, info, "manager_unavailable:provider_unreachable"
        info = {"step_id": call.step_id, "provider": call.provider_kind, "model": call.model, "is_mock": call.is_mock}
        try:
            verdict = prompts.parse(prompts.Verdict, call.text)
            verdict.check(len(criteria))
            return verdict, info, None
        except ValueError as e:
            error = str(e)
            manager.mark_invalid(db, call.step_id, error)
            db.commit()
    return None, info, "invalid_verdict"


async def review_submission(db: Session, submission_id: int) -> str:
    """Review one submission. Safe to call again: a decided submission is left alone."""
    sub = db.get(TaskSubmission, submission_id)
    if sub is None:
        return "missing"
    task = db.get(Task, sub.task_id, populate_existing=True)
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    db.refresh(sub)
    if wf is None or wf.kind != KIND:
        db.rollback()
        return "not_workforce"
    latest = latest_submission(db, task.id)
    if sub.decision is not None or latest is None or latest.id != sub.id or (sub.review or {}).get("needs_human"):
        db.rollback()
        return "already_reviewed"
    if task.lifecycle == L.SUBMITTED.value:
        move(db, task, L.UNDER_REVIEW, actor="manager", reason="review started",
             details={"submission_id": sub.id, "version": sub.version})
        task.review_status = "pending"
    elif task.lifecycle != L.UNDER_REVIEW.value:
        db.rollback()
        return "not_reviewable"
    policy, limit = policy_of(db, wf)
    db.commit()  # the lock is released while the model is asked

    content_ok = bool(sub.content.strip()) and len(sub.content) <= MAX_CONTENT_CHARS
    flagged = prompts.looks_like_injection(sub.content)
    secret = contains_credential(sub.content)
    # WF-14: a development task is applied to its branch and tested by the platform before any model is asked
    dev = await devtasks.apply_and_test(db, task, sub) if devtasks.is_dev_task(task) and content_ok else None
    if secret:  # something shaped like a credential: it is not sent to any model — a person looks at it first
        verdict, info, problem = None, {}, "result_contains_secret_like_text"
    elif dev is not None and not dev["ok"]:  # the platform's own check failed: no model decides over it
        verdict, info, problem = None, {}, None
    else:
        verdict, info, problem = await _verdict(db, wf, task, sub)

    wf = db.get(Workflow, wf.id, with_for_update=True, populate_existing=True)
    db.refresh(task)
    db.refresh(sub)
    latest = latest_submission(db, task.id)
    if task.lifecycle != L.UNDER_REVIEW.value or sub.decision is not None or latest.id != sub.id:
        db.rollback()  # a person decided, or the task was cancelled, while the model was answering
        return "superseded"
    used = revisions_used(db, task.id)
    if dev is not None and not dev["ok"] and not secret:
        out = dev_outcome(dev, used=used, limit=limit)
    else:
        out = outcome_of(verdict, content_ok=content_ok, flagged=flagged, policy=policy, used=used, limit=limit,
                         problem=problem)
    earlier = sub.review or {}
    checks = [{"check": "result_present", "passed": content_ok, "chars": len(sub.content)}]
    if dev is not None:
        checks.append({"check": "files_applied", "passed": bool(dev.get("head_commit")) and dev["code"] not in (
            "no_file_blocks", "apply_failed", "no_changes"), "code": dev["code"]})
        if dev["code"] == "merge_conflicts":
            checks.append({"check": "merges_cleanly", "passed": False})
        tests = dev.get("tests") or {}
        checks.append({"check": "tests", "passed": bool(tests.get("passed")) if tests.get("ran") else None,
                       "ran": bool(tests.get("ran")), "code": dev["code"]})
        record = devtasks.verification_record(dev, sub)
        if record is not None:
            task.evidence = [*(task.evidence or []), {**record, "at": datetime.now(UTC).isoformat()}]
    sub.review = {
        **({"retries": earlier["retries"]} if earlier.get("retries") else {}),  # a person asked for this review again
        "checks": checks, **({"dev": dev} if dev is not None else {}),
        "verdict": verdict.model_dump() if verdict else None,
        # which of the Manager's quotes really occur in the submission (used by the report's classes)
        "evidence_verified": ({str(c.index): quote_in(c.evidence, sub.content) for c in verdict.criteria if c.met}
                              if verdict else {}),
        "flagged_injection": flagged, "flagged_secret": secret, "policy": policy, "revisions_used": used,
        "max_revisions": limit,
        "manager": info or None, "reviewed_at": datetime.now(UTC).isoformat(), **out,
        "needs_human": out["outcome"] == "needs_human"}
    flag_modified(sub, "review")
    feedback = (verdict.feedback if verdict else "") or ""
    if out.get("overridden") == "approve_with_unmet_criteria":
        feedback = (feedback + "\n" if feedback else "") + "Not accepted: acceptance criteria " + ", ".join(
            str(i) for i in out["unmet"]) + " were not shown to be met."
    if out.get("overridden") == "empty_result":
        feedback = "The result was empty."
    if out.get("overridden") == "dev_check_failed":
        feedback = dev["feedback"]
    if out["outcome"] == "approve":
        _accept(db, wf, task, sub, decided_by="manager", note=verdict.summary if verdict else "")
    elif out["outcome"] == "revise":
        _revise(db, task, sub, decided_by="manager", feedback=feedback or "Address every acceptance criterion.")
    elif out["outcome"] == "reject":
        _reject(db, task, sub, decided_by="manager", feedback=feedback or "The result was rejected.")
    else:
        log_activity(db, "workforce.review_escalated", category="workforce", level="warning",
                     organization_id=task.organization_id, project_id=task.project_id, task_id=task.id,
                     actor="manager", details={"submission_id": sub.id, "version": sub.version,
                                               "reason": out.get("reason"), "advisory": out.get("advisory")})
    request_tick(db, wf, f"task {task.id} reviewed: {out['outcome']}")
    db.commit()
    return out["outcome"]


# ---------------- applying a decision ----------------
def _person(decided_by: str) -> int | None:
    """The user id behind a `user:<id>` actor, so that a person's decision is found by user in the audit trail."""
    return int(decided_by[5:]) if decided_by.startswith("user:") and decided_by[5:].isdigit() else None


def _decide(sub: TaskSubmission, decision: str, decided_by: str) -> None:
    sub.decision, sub.decided_by, sub.decided_at = decision, decided_by[:64], datetime.now(UTC)


def _accept(db: Session, wf: Workflow, task: Task, sub: TaskSubmission, *, decided_by: str, note: str = "") -> None:
    _decide(sub, "approved", decided_by)
    now = datetime.now(UTC)
    task.review_status = "approved"
    task.result = {**(task.result or {}), "output": sub.content, "submission_id": sub.id, "version": sub.version,
                   "accepted_by": decided_by}
    task.evidence = [*(task.evidence or []), {"kind": "review", "decision": "approved", "by": decided_by,
                                              "submission_id": sub.id, "version": sub.version,
                                              "sha256": sub.content_sha256, "note": note[:2000],
                                              "at": now.isoformat()}]
    task.error = None
    move(db, task, L.APPROVED, actor=decided_by, reason="review passed",
         details={"submission_id": sub.id, "version": sub.version, "sha256": sub.content_sha256})
    dev = (sub.review or {}).get("dev") or {}
    if devtasks.is_dev_task(task) and dev.get("head_commit"):
        # WF-14: accepting code is a request to merge exactly the tested commit; only a person's approval merges it
        requester = sub.submitted_by if (sub.submitted_by or "").startswith("user:") else "workforce"
        tests = dev.get("tests") or {}
        tested = ("passed" if tests.get("passed") else "FAILED") if tests.get("ran") else "not run"
        ap = request_approval(
            db, organization_id=task.organization_id, operation="git.merge_protected",
            title=f"Merge {dev['branch']} into {dev['base_branch']} (task #{task.id})", requested_by=requester,
            summary=(f"Version {sub.version} of “{task.title}”: {len(dev.get('files') or [])} file(s), "
                     f"tests {tested}, head {str(dev['head_commit'])[:12]}"
                     + (f" — also authorizes: {task.critical_operation}" if task.critical_operation else "")),
            payload=devtasks.merge_payload(task, sub, dev),
            evidence=[{"kind": "review", "by": decided_by, "note": note[:2000]},
                      {"kind": "submission", "submission_id": sub.id, "version": sub.version,
                       "sha256": sub.content_sha256, "mode": sub.mode, "source": sub.source, "is_mock": sub.is_mock,
                       "submitted_by": sub.submitted_by},
                      {"kind": "changes", "files": [f["path"] for f in dev.get("files") or []][:200],
                       "branch": dev["branch"], "head_commit": dev["head_commit"]},
                      {"kind": "tests", **{k: tests.get(k) for k in ("ran", "passed", "command", "exit_code",
                                                                      "duration_s")}}],
            agent_id=task.assigned_agent_id, project_id=task.project_id, task_id=task.id,
            execution_id=sub.execution_id, idempotency_key=f"wf-task:{task.id}:v{sub.version}:merge")
        task.result = {**task.result, "approval_id": ap.id}
    elif task.critical_operation:
        payload = {"task_id": task.id, "operation": task.critical_operation, "submission_id": sub.id,
                   "version": sub.version, "content_sha256": sub.content_sha256}
        # Who asks: the person who submitted the result, when a person did the work — the Approval Center never
        # lets a requester decide their own request, so the author of a critical result cannot be the one who
        # approves it. An Automatic worker's result is requested by the platform; agents hold no approval right.
        requester = sub.submitted_by if (sub.submitted_by or "").startswith("user:") else "workforce"
        ap = request_approval(
            db, organization_id=task.organization_id, operation=task.critical_operation,
            title=f"Task #{task.id}: {task.title}", requested_by=requester,
            summary=f"Accepting version {sub.version} of this result authorizes: {task.critical_operation}",
            payload=payload,
            evidence=[{"kind": "review", "by": decided_by, "note": note[:2000]},
                      {"kind": "submission", "submission_id": sub.id, "version": sub.version,
                       "sha256": sub.content_sha256, "mode": sub.mode, "source": sub.source, "is_mock": sub.is_mock,
                       "submitted_by": sub.submitted_by}],
            agent_id=task.assigned_agent_id, project_id=task.project_id, task_id=task.id,
            execution_id=sub.execution_id, idempotency_key=f"wf-task:{task.id}:v{sub.version}:approval")
        task.result = {**task.result, "approval_id": ap.id}
    else:
        move(db, task, L.COMPLETED, actor=decided_by, reason="accepted; no further approval required",
             details={"submission_id": sub.id, "version": sub.version})
    log_activity(db, "workforce.result_approved", category="workforce", organization_id=task.organization_id,
                 project_id=task.project_id, task_id=task.id, actor=decided_by,
                 user_id=_person(decided_by), details={"submission_id": sub.id, "version": sub.version, "sha256": sub.content_sha256,
                          "needs_approval": bool(task.critical_operation)})


def _revise(db: Session, task: Task, sub: TaskSubmission, *, decided_by: str, feedback: str) -> None:
    _decide(sub, "revision_requested", decided_by)
    task.review_status = "revise"
    notes = [*(task.result or {}).get("review_notes", []), feedback[:4000]]
    task.result = {**(task.result or {}), "review_notes": notes[-10:]}
    task.max_attempts = max(task.max_attempts, task.attempts + 1)  # a requested revision is not a failure
    move(db, task, L.REVISION_REQUESTED, actor=decided_by, reason=feedback[:500],
         details={"submission_id": sub.id, "version": sub.version})
    log_activity(db, "workforce.revision_requested", category="workforce", organization_id=task.organization_id,
                 project_id=task.project_id, task_id=task.id, actor=decided_by,
                 user_id=_person(decided_by), details={"submission_id": sub.id, "version": sub.version})


def _reject(db: Session, task: Task, sub: TaskSubmission, *, decided_by: str, feedback: str) -> None:
    _decide(sub, "rejected", decided_by)
    task.review_status, task.error = "rejected", f"rejected in review: {feedback}"[:2000]
    move(db, task, L.REJECTED, actor=decided_by, reason=feedback[:500],
         details={"submission_id": sub.id, "version": sub.version})
    log_activity(db, "workforce.result_rejected", category="workforce", level="warning",
                 organization_id=task.organization_id, project_id=task.project_id, task_id=task.id,
                 actor=decided_by, user_id=_person(decided_by),
                 details={"submission_id": sub.id, "version": sub.version})


def decide_by_human(db: Session, task: Task, user: User, decision: str, note: str) -> Task:
    """A person's review of the latest submission. A person may decide whenever a result is under review — also
    while, or instead of, the Manager. Commits."""
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    if wf is None or wf.kind != KIND:
        raise ReviewError("not_a_workforce_task")
    if task.lifecycle == L.SUBMITTED.value:
        move(db, task, L.UNDER_REVIEW, actor=f"user:{user.id}", reason="review started by a person")
    if task.lifecycle != L.UNDER_REVIEW.value:
        raise ReviewError(f"task_not_under_review:{task.lifecycle}")
    sub = latest_submission(db, task.id)
    if sub is None or sub.decision is not None:
        raise ReviewError("no_undecided_submission")
    actor = f"user:{user.id}"
    review = dict(sub.review or {})
    review["human"] = {"by": actor, "decision": decision, "note": note[:4000], "at": datetime.now(UTC).isoformat(),
                       # the same person delivered and accepted this result: shown as such in the report
                       "self_reviewed": sub.submitted_by == actor,
                       "unmet_at_decision": review.get("unmet")}
    review["needs_human"] = False
    sub.review = review
    flag_modified(sub, "review")
    if decision == "approve":
        _accept(db, wf, task, sub, decided_by=actor, note=note)
    elif decision == "revise":
        _revise(db, task, sub, decided_by=actor, feedback=note)
    else:
        _reject(db, task, sub, decided_by=actor, feedback=note)
    request_tick(db, wf, f"task {task.id} reviewed by {actor}: {decision}")
    db.commit()
    return task


def ask_manager_again(db: Session, task: Task, user: User) -> TaskSubmission:
    """Clear an escalation so the Manager reviews the submission once more (for example after its model was
    configured or came back). Commits; the review itself runs on the next tick."""
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    sub = latest_submission(db, task.id)
    if wf is None or task.lifecycle != L.UNDER_REVIEW.value or sub is None or sub.decision is not None:
        raise ReviewError(f"task_not_under_review:{task.lifecycle}")
    review = dict(sub.review or {})
    review["needs_human"] = False
    review.setdefault("retries", []).append({"by": f"user:{user.id}", "at": datetime.now(UTC).isoformat(),
                                             "previous_reason": review.get("reason")})
    sub.review = review
    flag_modified(sub, "review")
    log_activity(db, "workforce.review_retry_requested", category="workforce", organization_id=task.organization_id,
                 project_id=task.project_id, task_id=task.id, user_id=user.id, details={"submission_id": sub.id})
    request_tick(db, wf, f"task {task.id}: review again")
    db.commit()
    return sub


# ---------------- Approval Center ----------------
def _on_approval_decision(db: Session, a: Approval) -> None:
    """A human decided on a critical operation: settle the workforce task that was waiting for it."""
    if not a.task_id:
        return
    task = db.get(Task, a.task_id)
    if task is None or task.lifecycle != L.APPROVED.value or (task.result or {}).get("approval_id") != a.id:
        return
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    if wf is None or wf.kind != KIND:
        return
    db.refresh(task)
    actor = f"user:{a.approver_id}" if a.approver_id else "approvals"
    sub = db.get(TaskSubmission, (a.payload or {}).get("submission_id") or 0)
    # The approved request names one submission by hash. Anything else is not what the person approved.
    intact = (sub is not None and sub.task_id == task.id and sub.content_sha256 == a.payload.get("content_sha256")
              and (task.result or {}).get("submission_id") == sub.id
              and payload_hash(a.payload) == next((e.get("sha256") for e in a.evidence or []
                                                   if e.get("kind") == "payload"), None))
    now = datetime.now(UTC)
    merge = a.operation == "git.merge_protected"
    if merge and a.status == "approved" and intact:  # approved, but the merge itself failed (conflict, moved branch)
        error = next((e.get("error") for e in reversed(a.evidence or []) if e.get("kind") == "execution_failed"),
                     "the merge did not run")
        task.review_status = "revise"
        notes = [*(task.result or {}).get("review_notes", []), f"MERGE FAILED: {error}"]
        task.result = {**(task.result or {}), "review_notes": notes[-10:]}
        task.max_attempts = max(task.max_attempts, task.attempts + 1)
        move(db, task, L.REVISION_REQUESTED, actor="system", reason=f"approved, but the merge failed: {error}"[:500],
             details={"approval_id": a.id})
    elif a.status in ("approved", "executed") and intact:
        if merge:
            done = next((e.get("result") for e in reversed(a.evidence or []) if e.get("kind") == "execution"), {})
            task.evidence = [*(task.evidence or []), {"kind": "merge", "approval_id": a.id,
                                                      "merged": (done or {}).get("merged"),
                                                      "into": (done or {}).get("into"),
                                                      "merge_commit": (done or {}).get("merge_commit"),
                                                      "at": now.isoformat()}]
            task.result = {**(task.result or {}), "merged": done or {}}
        move(db, task, L.COMPLETED, actor=actor, reason=("merged after approval in the Approval Center" if merge
                                                        else "approved in the Approval Center"),
             details={"approval_id": a.id, "submission_id": sub.id, "version": sub.version})
    elif a.status in ("approved", "executed"):
        log_activity(db, "workforce.approval_mismatch", category="workforce", level="error",
                     organization_id=task.organization_id, task_id=task.id, details={"approval_id": a.id})
        task.error = "the approved request does not match the reviewed result"
        move(db, task, L.REJECTED, actor="system", reason="approval does not match the reviewed result",
             details={"approval_id": a.id})
    elif a.status == "changes_requested":
        task.review_status = "revise"
        notes = [*(task.result or {}).get("review_notes", []), f"APPROVER: {a.reason}"]
        task.result = {**(task.result or {}), "review_notes": notes[-10:]}
        task.max_attempts = max(task.max_attempts, task.attempts + 1)
        move(db, task, L.REVISION_REQUESTED, actor=actor, reason=f"approver requested changes: {a.reason}"[:500],
             details={"approval_id": a.id})
    else:
        task.error = f"approval {a.status}: {a.reason}"[:2000]
        move(db, task, L.REJECTED, actor=actor, reason=f"approval {a.status}"[:500], details={"approval_id": a.id})
    task.evidence = [*(task.evidence or []), {"kind": "approval", "approval_id": a.id, "status": a.status,
                                              "approver_id": a.approver_id, "at": now.isoformat()}]
    request_tick(db, wf, f"task {task.id} approval {a.status}")


on_decision(_on_approval_decision)


# ---------------- people changing the course of a task ----------------
def reopen(db: Session, task: Task, user: User, note: str) -> Task:
    """A failed or rejected task gets another chance. Only a person can do this. Commits."""
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    if wf is None or wf.kind != KIND:
        raise ReviewError("not_a_workforce_task")
    if task.lifecycle not in (L.FAILED.value, L.REJECTED.value):
        raise ReviewError(f"task_not_settled_unsuccessfully:{task.lifecycle}")
    if wf.status == "cancelled":
        raise ReviewError("objective_cancelled")
    result = dict(task.result or {})
    result["auto_failures"] = 0
    result.pop("declined_by_agents", None)
    task.result = result
    task.error, task.review_status = None, "none"
    task.max_attempts = max(task.max_attempts, task.attempts + 1)
    move(db, task, L.READY, actor=f"user:{user.id}", reason=f"reopened: {note}"[:500])
    reopen_if_needed(db, wf)
    log_activity(db, "workforce.task_reopened", category="workforce", level="warning",
                 organization_id=task.organization_id, project_id=task.project_id, task_id=task.id, user_id=user.id,
                 details={"note": note[:500]})
    request_tick(db, wf, f"task {task.id} reopened")
    db.commit()
    return task
