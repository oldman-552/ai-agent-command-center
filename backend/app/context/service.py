"""Task context assembly from the database (CX-01..03 + MEM-02/03 wiring)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.context.assembler import Assembled, Item, assemble
from app.memory import store
from app.models import Agent, Task

MAX_MEMORIES = 12


def budget_for(agent: Agent | None) -> int:
    pol = (agent.context_policy or {}) if agent else {}
    try:
        return max(1_000, min(int(pol.get("max_context_tokens", 8_000)), 200_000))
    except (TypeError, ValueError):
        return 8_000


def task_items(db: Session, task: Task, deps: list[Task], agent: Agent | None) -> list[Item]:
    req = [f"TASK: {task.title}", ""]
    if task.objective:  # AI Workforce tasks say why they exist and what form the result must have
        req += [f"WHY THIS TASK EXISTS: {task.objective}", ""]
    req += [task.instructions or task.description or "", ""]
    if task.expected_output:
        req += ["EXPECTED OUTPUT:", task.expected_output, ""]
    req += ["ACCEPTANCE CRITERIA:", *[f"- {c}" for c in task.acceptance_criteria or []]]
    items = [Item("requirements", "task", "\n".join(req).strip(), score=10.0, source={"task_id": task.id})]
    if agent is not None:  # skills a human reviewed, enabled and assigned to this agent (MCP-04)
        from sqlalchemy import select

        from app.models import Skill
        for sk in db.scalars(select(Skill).where(Skill.organization_id == task.organization_id,
                                                 Skill.enabled.is_(True)).order_by(Skill.id)):
            if agent.id in (sk.agent_ids or []):
                items.append(Item("requirements", f"skill: {sk.name}",
                                  f"SKILL '{sk.name}' (enabled by a human after review):\n{sk.instructions}",
                                  score=8.0, source={"skill_id": sk.id}))
    for note in (task.result or {}).get("review_notes", []):
        items.append(Item("user", "review note", f"REVIEWER REQUESTED CHANGES: {note}", score=9.0))
    for d in deps:
        out = str((d.result or {}).get("output", ""))
        if out:
            items.append(Item("tool_output", f"output of prerequisite task '{d.title}'", out, score=5.0,
                              source={"task_id": d.id}))
    if task.project_id is not None:
        from app.context.assembler import profile
        query = f"{task.title}\n{task.description or ''}\n" + "\n".join(task.acceptance_criteria or [])
        hits = store.search(db, organization_id=task.organization_id, query=query, project_id=task.project_id,
                            agent_id=agent.id if agent else None, types=profile(task.task_type)["memory_types"],
                            limit=MAX_MEMORIES)
        for h in hits:
            m = h.memory
            items.append(Item("retrieved", f"memory #{m.id}: {m.title}", m.content, score=1.0 + h.score,
                              source={"memory_id": m.id, "type": m.memory_type, "verification": m.verification,
                                      "version": m.version}))
    return items


def for_task(db: Session, task: Task, deps: list[Task], agent: Agent | None) -> Assembled:
    return assemble(task_items(db, task, deps, agent), task_type=task.task_type, budget=budget_for(agent),
                    agent_instructions=agent.system_instructions if agent else "")
