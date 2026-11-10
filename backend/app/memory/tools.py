"""`memory.write` runtime tool: agents may *propose* memories in their own project. Proposals are stored as
`suggestion` (never verified) with provenance pointing at the execution; a human verifies or corrects them."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.db import session_factory
from app.memory import store
from app.models import Execution, Task
from app.runtime.service import register_tool

AGENT_TYPES = {"project", "task", "technical_decision", "codebase", "execution", "lesson"}


async def memory_write(ex: Execution, args: dict[str, Any]) -> dict[str, Any]:
    mtype = str(args.get("type", "lesson"))
    if mtype not in AGENT_TYPES:
        return {"error": "refused", "reason": f"agents may propose only {sorted(AGENT_TYPES)}"}
    with session_factory()() as db:
        task = db.get(Task, ex.task_id) if ex.task_id else None
        project_id = task.project_id if task else ex.project_id
        if project_id is None:
            return {"error": "refused", "reason": "memories need a project scope"}
        try:
            m = store.create(db, organization_id=ex.organization_id, memory_type=mtype,
                             title=str(args.get("title", ""))[:255] or "untitled", content=str(args.get("content", "")),
                             source={"kind": "agent", "ref": f"agent:{ex.agent_id}", "execution_id": ex.id},
                             verification="suggestion", project_id=project_id, agent_id=ex.agent_id,
                             task_id=ex.task_id)
        except HTTPException as e:
            return {"error": "refused", "reason": str(e.detail)}
        db.commit()
        return {"memory_id": m.id, "verification": m.verification}


register_tool("memory.write", memory_write)
