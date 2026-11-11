"""The consolidated result of an objective (WF-12).

One report per state of the objective. Every task result is put into exactly one class, and the class is computed
by the platform from what it recorded — never from what a model wrote about the work:

    verified          the platform itself checked the result (for example: files applied to a branch, tests run in
                      the sandbox). Text-only work cannot be in this class.
    human_approved    a person accepted the result (in review or in the Approval Center)
    worker_proposal   the Manager accepted it, and for every acceptance criterion the Manager quoted words that
                      really occur in the result
    unverified_claim  the Manager accepted it, but at least one criterion rests on the Manager's judgement alone
    failed            the task failed or its result was rejected
    incomplete        the task was cancelled, is blocked, or is not finished

"Facts" are statements about the process that the platform recorded itself (who delivered what, when, through which
provider, which hash, who accepted it). The Manager's closing summary is added last, labelled as model text; it
cannot change a class. Anything that passed through a mock provider or the fake bridge is labelled mock.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Agent, Task, TaskSubmission, TaskTransition, Workflow, WorkforceReport
from app.models import TaskLifecycle as L
from app.security.audit import log_activity
from app.workforce import manager, prompts, service
from app.workforce.scheduler import KIND

log = logging.getLogger("workforce.report")
CLASSES = ("verified", "human_approved", "worker_proposal", "unverified_claim", "failed", "incomplete")
EXCERPT_CHARS = 1500
FINISHED_OBJECTIVE = ("completed", "failed", "cancelled")


def classify(task: Task, accepted: TaskSubmission | None) -> dict[str, Any]:
    """The class of one task result and the recorded evidence it rests on. Pure: no model, no clock."""
    lc = task.lifecycle
    if lc != L.COMPLETED.value or accepted is None:
        if lc in (L.FAILED.value, L.REJECTED.value):
            last = ((task.result or {}).get("failures") or [{}])[-1]
            return {"class": "failed", "reason": task.error or lc.lower(),
                    "basis": {"lifecycle": lc, "failure_code": last.get("code"),
                              "failure_category": last.get("category")}}
        waiting = ((task.result or {}).get("waiting") or {}).get("reason")
        return {"class": "incomplete", "reason": task.error or waiting or lc.lower(), "basis": {"lifecycle": lc}}
    review = accepted.review or {}
    evidence = task.evidence or []
    machine = [e for e in evidence if e.get("kind") == "verification" and e.get("passed") is True
               and e.get("submission_id") == accepted.id]
    approval = next((e for e in reversed(evidence) if e.get("kind") == "approval"
                     and e.get("status") in ("approved", "executed")), None)
    by_person = str(accepted.decided_by or "").startswith("user:")
    verdict = review.get("verdict") or {}
    met = [c["index"] for c in verdict.get("criteria", []) if c.get("met")]
    quoted = [i for i in met if (review.get("evidence_verified") or {}).get(str(i))]
    basis: dict[str, Any] = {
        "accepted_by": accepted.decided_by, "submission_id": accepted.id, "version": accepted.version,
        "sha256": accepted.content_sha256, "criteria": len(task.acceptance_criteria or []),
        "criteria_met_by_manager": len(met), "criteria_with_verified_quote": len(quoted),
        "approval_id": approval.get("approval_id") if approval else None,
        "self_reviewed": bool((review.get("human") or {}).get("self_reviewed")),
        "reviewed_by_mock_manager": bool((review.get("manager") or {}).get("is_mock"))}
    if machine:
        return {"class": "verified", "reason": None,
                "basis": {**basis, "verification": [e.get("check") for e in machine]}}
    if by_person or approval:
        return {"class": "human_approved", "reason": None, "basis": basis}
    if len(quoted) == len(task.acceptance_criteria or []) and quoted:
        return {"class": "worker_proposal", "reason": None, "basis": basis}
    return {"class": "unverified_claim",
            "reason": "accepted by the Manager; not every criterion is backed by words found in the result",
            "basis": {**basis, "criteria_without_quote": sorted(set(range(1, basis["criteria"] + 1)) - set(quoted))}}


def _items(db: Session, wf: Workflow) -> list[dict[str, Any]]:
    out = []
    for t in db.scalars(select(Task).where(Task.workflow_id == wf.id).order_by(Task.id)):
        sid = (t.result or {}).get("submission_id")
        accepted = db.get(TaskSubmission, sid) if sid and t.lifecycle == L.COMPLETED.value else None
        c = classify(t, accepted)
        if t.lifecycle == L.CANCELLED.value:  # who cancelled it and why is in the task's history, not in a model's words
            last = db.scalar(select(TaskTransition).where(
                TaskTransition.task_id == t.id, TaskTransition.to_state == L.CANCELLED.value)
                .order_by(TaskTransition.ts.desc(), TaskTransition.id.desc()).limit(1))
            if last is not None:
                c["reason"] = last.reason or c["reason"]
                c["basis"] = {**c["basis"], "cancelled_by": last.actor}
        w = service.worker_for_agent(db, t.assigned_agent_id)
        agent = db.get(Agent, t.assigned_agent_id) if t.assigned_agent_id else None
        versions = db.scalar(select(func.count()).select_from(TaskSubmission).where(
            TaskSubmission.task_id == t.id)) or 0
        mock_reasons = []
        if accepted is not None and accepted.is_mock:
            mock_reasons.append("delivered through a mock provider or the fake bridge")
        if c["basis"].get("reviewed_by_mock_manager"):
            mock_reasons.append("reviewed by the mock Manager model")
        out.append({
            "task_id": t.id, "title": t.title, "lifecycle": t.lifecycle, "class": c["class"], "reason": c["reason"],
            "basis": c["basis"], "worker": f"W{w.slot}" if w else None, "worker_name": agent.name if agent else None,
            "mode": accepted.mode if accepted else t.execution_mode, "attempts": t.attempts, "versions": versions,
            "assignment_source": t.assignment_source, "critical_operation": t.critical_operation,
            "submission": ({"id": accepted.id, "version": accepted.version, "sha256": accepted.content_sha256,
                            "source": accepted.source, "submitted_by": accepted.submitted_by,
                            "decided_by": accepted.decided_by, "decided_at": accepted.decided_at.isoformat()
                            if accepted.decided_at else None, "chars": len(accepted.content),
                            "attachments": len(accepted.attachments or []),
                            "provider_request_id": (accepted.provider or {}).get("request_id"),
                            "account_binding": ((accepted.provider or {}).get("account_binding") or {}).get("type")}
                           if accepted else None),
            "is_mock": bool(mock_reasons), "mock_reasons": mock_reasons,
            "excerpt": accepted.content[:EXCERPT_CHARS] if accepted else None})
    return out


def _facts(wf: Workflow, items: list[dict[str, Any]]) -> list[str]:
    """Statements about the process, each derived from rows the platform wrote itself."""
    by_class = {c: sum(1 for i in items if i["class"] == c) for c in CLASSES}
    done = sum(1 for i in items if i["lifecycle"] == L.COMPLETED.value)
    facts = [(f"{done} of {len(items)} tasks were completed; {by_class['failed']} failed or were rejected; "
              f"{by_class['incomplete']} were not finished.")]
    for i in items:
        s = i["submission"]
        if s is None:
            continue
        how = ("by a person" if i["mode"] == "HUMAN_ASSISTED" else
               f"automatically through {s['source']}" + (f" (request {s['provider_request_id']})"
                                                         if s["provider_request_id"] else ""))
        facts.append(f"Task #{i['task_id']} “{i['title']}”: version {s['version']} of the result "
                     f"(sha256 {s['sha256'][:12]}…) was delivered {how} for {i['worker'] or 'no worker'}"
                     f" and accepted by {s['decided_by']}"
                     + (f" at {s['decided_at']}" if s["decided_at"] else "") + ".")
    for i in items:
        if i["class"] == "verified":
            facts.append(f"Task #{i['task_id']} “{i['title']}”: the platform applied the delivered files to a branch "
                         "and ran the project's tests on exactly the accepted version; they passed.")
    if not by_class["verified"]:
        facts.append("The platform did not check the content of any result itself: the workers are text-only and "
                     "nothing was executed or tested.")
    if any(i["is_mock"] for i in items):
        facts.append("Some results came through a mock provider, the fake bridge or the mock Manager; they are "
                     "labelled MOCK and are not real AI or Arena results.")
    if ((wf.plan or {}).get("manager") or {}).get("is_mock"):
        facts.append("The plan — the tasks and their acceptance criteria — was written by the mock Manager, "
                     "not by a real model.")
    return facts


def state_hash(wf: Workflow, items: list[dict[str, Any]]) -> str:
    key = [wf.status, [(i["task_id"], i["lifecycle"], i["class"], (i["submission"] or {}).get("id"),
                        (i["submission"] or {}).get("sha256"), (i["submission"] or {}).get("decided_by"))
                       for i in items]]
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


def render_markdown(content: dict[str, Any]) -> str:
    titles = {"verified": "Verified by the platform", "human_approved": "Approved by a person",
              "worker_proposal": "Worker proposals (accepted by the Manager, criteria quoted from the result)",
              "unverified_claim": "Unverified claims (accepted on the Manager's judgement alone)",
              "failed": "Failed or rejected", "incomplete": "Incomplete"}
    lines = [f"# Report — {content['title']}", "",
             f"Status: **{content['status']}**" + (" — **contains MOCK results (not real AI / not Arena)**"
                                                   if content["is_mock"] else ""), "",
             "## Objective", "", content["objective"], "", "## Facts recorded by the platform", ""]
    lines += [f"- {f}" for f in content["facts"]]
    for cls in CLASSES:
        group = [i for i in content["items"] if i["class"] == cls]
        lines += ["", f"## {titles[cls]} ({len(group)})", ""]
        if not group:
            lines.append("_None._")
        for i in group:
            lines.append(f"### Task #{i['task_id']} — {i['title']}" + (" — MOCK" if i["is_mock"] else ""))
            lines.append(f"Worker: {i['worker'] or '-'} · mode: {i['mode'] or '-'} · attempts: {i['attempts']} · "
                         f"result versions: {i['versions']}")
            if i["reason"]:
                lines.append(f"Reason: {i['reason']}")
            if i["excerpt"]:
                lines += ["", "```text", i["excerpt"].replace("```", "ʼʼʼ"), "```"]
            lines.append("")
    n = content.get("narrative")
    lines += ["", "## Manager's summary (model text — not evidence)", ""]
    if n:
        lines += [n["summary"], ""] + [f"- {s}" for s in n.get("next_steps", [])]
        lines.append(f"\n_Written by {n['written_by']['model']}"
                     f"{' — MOCK' if n['written_by']['is_mock'] else ''}._")
    else:
        lines.append(f"_Not available: {content.get('narrative_error') or 'the Manager did not write one'}._")
    return "\n".join(lines).strip() + "\n"


async def _narrative(db: Session, wf: Workflow, items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None,
                                                                                    str | None, str | None]:
    nonce = prompts.new_nonce()
    error: str | None = None
    for attempt in range(1, prompts.MAX_ATTEMPTS + 1):
        try:
            call = await manager.ask(db, wf, "consolidate", prompts.REPORT_SYSTEM,
                                     prompts.report_user(objective=wf.objective, items=items, nonce=nonce,
                                                         error=error), attempt=attempt, max_tokens=4000)
        except (manager.ManagerFailed, manager.ManagerUnavailable) as e:
            return None, f"the Manager's model was not available ({str(e)[:200]})", None
        try:
            n = prompts.parse(prompts.Narrative, call.text)
        except ValueError as e:
            error = str(e)
            manager.mark_invalid(db, call.step_id, error)
            db.commit()
            continue
        return ({"summary": n.summary, "next_steps": n.next_steps,
                 "written_by": {"provider": call.provider_kind, "model": call.model, "is_mock": call.is_mock}},
                None, call.step_id)
    return None, "the Manager's summary was not valid", None


async def consolidate(db: Session, workflow_id: int) -> WorkforceReport | None:
    """Build the report for the current state of a finished objective. Idempotent per state."""
    wf = db.get(Workflow, workflow_id, populate_existing=True)
    if wf is None or wf.kind != KIND or wf.status not in FINISHED_OBJECTIVE:
        return None
    items = _items(db, wf)
    if not items:
        return None  # planning never produced a task: the objective's own error says why
    digest = state_hash(wf, items)
    existing = db.scalar(select(WorkforceReport).where(WorkforceReport.workflow_id == wf.id,
                                                       WorkforceReport.state_hash == digest))
    if existing is not None:
        return existing
    done = sum(1 for i in items if i["lifecycle"] == L.COMPLETED.value)
    status = "complete" if items and done == len(items) else "partial" if done else "failed"
    organization_id, project_id, title, objective = wf.organization_id, wf.project_id, wf.title, wf.objective
    narrative, narrative_error, step_id = await _narrative(db, wf, items)
    planned_by = (wf.plan or {}).get("manager") or None
    is_mock = (any(i["is_mock"] for i in items) or bool(narrative and narrative["written_by"]["is_mock"])
               or bool(planned_by and planned_by.get("is_mock")))
    content = {
        "title": title, "objective": objective, "status": status, "objective_status": wf.status,
        "planned_by": planned_by,
        "counts": {c: sum(1 for i in items if i["class"] == c) for c in CLASSES},
        "tasks": len(items), "completed": done, "facts": _facts(wf, items),
        "items": [{k: v for k, v in i.items()} for i in items],
        "narrative": narrative, "narrative_error": narrative_error, "is_mock": is_mock,
        "classes": list(CLASSES), "generated_at": datetime.now(UTC).isoformat()}
    version = (db.scalar(select(func.max(WorkforceReport.version)).where(
        WorkforceReport.workflow_id == workflow_id)) or 0) + 1
    report = WorkforceReport(organization_id=organization_id, workflow_id=workflow_id, version=version, status=status,
                             state_hash=digest, content=content, markdown=render_markdown(content), is_mock=is_mock,
                             manager_step_id=step_id)
    db.add(report)
    try:
        db.flush()
    except IntegrityError:  # another worker consolidated the same state at the same moment
        db.rollback()
        return db.scalar(select(WorkforceReport).where(WorkforceReport.workflow_id == workflow_id,
                                                       WorkforceReport.state_hash == digest))
    log_activity(db, "workforce.report_created", category="workforce", organization_id=organization_id,
                 project_id=project_id, actor="manager",
                 details={"workflow_id": workflow_id, "version": version, "status": status, "is_mock": is_mock,
                          "counts": content["counts"]})
    db.commit()
    return report


def latest(db: Session, workflow_id: int) -> WorkforceReport | None:
    return db.scalar(select(WorkforceReport).where(WorkforceReport.workflow_id == workflow_id)
                     .order_by(WorkforceReport.version.desc()).limit(1))


def report_out(r: WorkforceReport, *, with_markdown: bool = False) -> dict[str, Any]:
    out = {"id": r.id, "workflow_id": r.workflow_id, "version": r.version, "status": r.status, "is_mock": r.is_mock,
           "content": r.content, "created_at": r.created_at}
    if with_markdown:
        out["markdown"] = r.markdown
    return out
