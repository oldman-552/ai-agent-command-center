"""Human-Assisted work (WF-09): a person takes a task card, does the work by hand and submits the result.

It needs no bridge, no provider and no acknowledgement. The card shows the same brief an Automatic worker would
receive, as one message that can be pasted into Arena's own page; the person pastes the answer back, with optional
notes and attached files. What they submit is stored unchanged as the next version and reviewed like any result.

Attached files are stored outside the web root under a random name, limited in size and type, hashed, and only ever
served as a download to members of the same organization.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ExecutionMode, Task, TaskAttachment, TaskSubmission, User, Workflow
from app.models import TaskLifecycle as L
from app.security.audit import log_activity
from app.security.crypto import redact
from app.workforce import service
from app.workforce.lifecycle import move
from app.workforce.scheduler import KIND, request_tick, submit

MAX_CONTENT_CHARS = 400_000
MAX_NOTES_CHARS = 8_000
MAX_ATTACHMENTS = 10
# What a result is usually made of. Everything is served as a download, never rendered by the platform.
ALLOWED_EXTENSIONS = {".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".xml", ".html", ".css", ".js", ".ts",
                      ".tsx", ".jsx", ".py", ".sql", ".sh", ".ps1", ".bat", ".diff", ".patch", ".pdf", ".png", ".jpg",
                      ".jpeg", ".gif", ".webp", ".zip", ".docx", ".xlsx", ".pptx"}
_NAME = re.compile(r"[^\w.\- ()\[\]]+", re.UNICODE)


class HumanWorkError(ValueError):
    """Something a person tried is not possible now (the API turns it into 409 or 422)."""

    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def can_work(task: Task, worker, user: User, permissions: set[str]) -> bool:
    """Who may take a card: anyone allowed to do workforce work — or only the worker's named person, when one is
    set (people who configure the workforce may always step in)."""
    if "workforce.work" not in permissions:
        return False
    if worker is None or worker.assignee_user_id is None:
        return True
    return worker.assignee_user_id == user.id or "workforce.manage" in permissions


def _load(db: Session, task: Task) -> tuple[Workflow, Any]:
    wf = db.get(Workflow, task.workflow_id, with_for_update=True, populate_existing=True) if task.workflow_id else None
    db.refresh(task)
    if wf is None or wf.kind != KIND:
        raise HumanWorkError("not_a_workforce_task")
    return wf, service.worker_for_agent(db, task.assigned_agent_id)


def accept(db: Session, task: Task, user: User, permissions: set[str]) -> Task:
    _, worker = _load(db, task)
    if task.lifecycle != L.WAITING_FOR_ACCEPTANCE.value:
        raise HumanWorkError(f"task_not_waiting_for_acceptance:{task.lifecycle}")
    if not can_work(task, worker, user, permissions):
        raise HumanWorkError("task_reserved_for_another_person", 403)
    task.accepted_by_user_id, task.accepted_at = user.id, datetime.now(UTC)
    move(db, task, L.ACCEPTED, actor=f"user:{user.id}", reason="accepted by a person",
         details={"attempt": task.attempts})
    log_activity(db, "workforce.task_accepted", category="workforce", organization_id=task.organization_id,
                 project_id=task.project_id, task_id=task.id, user_id=user.id, details={"attempt": task.attempts})
    db.commit()
    return task


def decline(db: Session, task: Task, user: User, permissions: set[str], reason: str) -> Task:
    """Give the card back. The task returns to the queue and is offered to another eligible worker; if there is
    none, it waits for a manual reassignment and says so."""
    wf, worker = _load(db, task)
    if task.lifecycle not in (L.WAITING_FOR_ACCEPTANCE.value, L.ACCEPTED.value):
        raise HumanWorkError(f"task_not_with_a_person:{task.lifecycle}")
    if not can_work(task, worker, user, permissions):
        raise HumanWorkError("task_reserved_for_another_person", 403)
    if task.lifecycle == L.ACCEPTED.value and task.accepted_by_user_id != user.id and (
            "workforce.manage" not in permissions):
        raise HumanWorkError("accepted_by_another_person", 403)
    result = dict(task.result or {})
    declined = set(result.get("declined_by_agents", []))
    if task.assigned_agent_id:
        declined.add(task.assigned_agent_id)
    result["declined_by_agents"] = sorted(declined)
    result["declines"] = [*result.get("declines", []), {"by": f"user:{user.id}", "reason": reason[:500],
                                                        "worker_agent_id": task.assigned_agent_id,
                                                        "at": datetime.now(UTC).isoformat()}][-20:]
    task.result = result
    task.accepted_by_user_id = task.accepted_at = None
    move(db, task, L.READY, actor=f"user:{user.id}", reason=f"declined: {reason}"[:500],
         details={"worker_agent_id": task.assigned_agent_id})
    log_activity(db, "workforce.task_declined", category="workforce", level="warning",
                 organization_id=task.organization_id, project_id=task.project_id, task_id=task.id, user_id=user.id,
                 details={"reason": reason[:500]})
    request_tick(db, wf, f"task {task.id} declined")
    db.commit()
    return task


def submit_work(db: Session, task: Task, user: User, *, content: str, notes: str = "",
                attachment_ids: list[str] | None = None) -> TaskSubmission:
    """Store what a person delivers as the next version of the result and send it to review. Commits."""
    wf, worker = _load(db, task)
    if task.lifecycle != L.ACCEPTED.value:
        raise HumanWorkError(f"task_not_accepted:{task.lifecycle}")
    if task.accepted_by_user_id != user.id:
        raise HumanWorkError("accepted_by_another_person", 403)
    if not content.strip():
        raise HumanWorkError("content_required", 422)
    if len(content) > MAX_CONTENT_CHARS or len(notes) > MAX_NOTES_CHARS:
        raise HumanWorkError("too_long", 422)
    ids = list(dict.fromkeys(attachment_ids or []))
    if len(ids) > MAX_ATTACHMENTS:
        raise HumanWorkError("too_many_attachments", 422)
    files = []
    for aid in ids:
        a = db.get(TaskAttachment, aid)
        if a is None or a.organization_id != task.organization_id or a.task_id != task.id or (
                a.submission_id is not None):
            raise HumanWorkError("invalid_attachment", 422)
        files.append(a)
    sub = submit(db, task, mode=ExecutionMode.HUMAN_ASSISTED.value, content=content, submitted_by=f"user:{user.id}",
                 notes=redact(notes), worker=worker,
                 attachments=[{"id": a.id, "filename": a.filename, "size": a.size, "sha256": a.sha256,
                               "content_type": a.content_type} for a in files])
    for a in files:
        a.submission_id = sub.id
    move(db, task, L.SUBMITTED, actor=f"user:{user.id}", reason="result submitted by a person",
         details={"submission_id": sub.id, "version": sub.version, "attachments": len(files)})
    request_tick(db, wf, f"task {task.id} submitted by a person")
    db.commit()
    return sub


# ---------------- attachments ----------------
def storage_root() -> Path:
    return Path(get_settings().workforce_attachments_dir).resolve()


def safe_filename(name: str) -> str:
    base = Path(str(name or "").replace("\\", "/")).name.strip()
    base = _NAME.sub("_", base).strip(" .")
    return (base or "file")[:200]


def store_attachment(db: Session, task: Task, user: User, *, filename: str, content_type: str | None,
                     data: bytes) -> TaskAttachment:
    """Keep an uploaded file for the result the person is preparing. Commits."""
    _load(db, task)
    if task.lifecycle != L.ACCEPTED.value or task.accepted_by_user_id != user.id:
        raise HumanWorkError("task_not_accepted_by_you")
    name = safe_filename(filename)
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HumanWorkError("file_type_not_allowed", 422)
    limit = get_settings().workforce_attachment_max_bytes
    if not data:
        raise HumanWorkError("empty_file", 422)
    if len(data) > limit:
        raise HumanWorkError("file_too_large", 413)
    pending = db.scalars(select(TaskAttachment).where(TaskAttachment.task_id == task.id,
                                                      TaskAttachment.submission_id.is_(None))).all()
    if len(pending) >= MAX_ATTACHMENTS:
        raise HumanWorkError("too_many_attachments", 422)
    aid = str(uuid.uuid4())
    folder = storage_root() / str(task.organization_id) / str(task.id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / aid  # never the user's file name
    path.write_bytes(data)
    path.chmod(0o600)
    a = TaskAttachment(id=aid, organization_id=task.organization_id, task_id=task.id, filename=name,
                       content_type=(content_type or "application/octet-stream")[:128], size=len(data),
                       sha256=hashlib.sha256(data).hexdigest(), storage_path=str(path),
                       uploaded_by_user_id=user.id)
    db.add(a)
    log_activity(db, "workforce.attachment_added", category="workforce", organization_id=task.organization_id,
                 project_id=task.project_id, task_id=task.id, user_id=user.id,
                 details={"attachment_id": aid, "filename": name, "size": len(data), "sha256": a.sha256})
    db.commit()
    return a


def remove_attachment(db: Session, a: TaskAttachment, user: User) -> None:
    """Only a file that is not part of a submission yet can be removed, by the person who added it."""
    if a.submission_id is not None:
        raise HumanWorkError("attachment_is_part_of_a_submission")
    if a.uploaded_by_user_id != user.id:
        raise HumanWorkError("not_your_attachment", 403)
    path = attachment_path(a)
    if path is not None:
        path.unlink(missing_ok=True)
    db.delete(a)
    db.commit()


def attachment_path(a: TaskAttachment) -> Path | None:
    """The stored file, only if it really lies inside the attachment store."""
    path = Path(a.storage_path).resolve()
    root = storage_root()
    if root not in path.parents or not path.is_file():
        return None
    return path


def attachment_out(a: TaskAttachment) -> dict[str, Any]:
    return {"id": a.id, "task_id": a.task_id, "submission_id": a.submission_id, "filename": a.filename,
            "content_type": a.content_type, "size": a.size, "sha256": a.sha256, "created_at": a.created_at,
            "uploaded_by_user_id": a.uploaded_by_user_id}
