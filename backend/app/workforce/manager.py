"""The Manager: one model call per step, recorded, and the planning graph (WF-06, WF-18).

    analyze ─► plan ─► validate ─┬─(valid)──► assign ─► END            every node is checkpointed in PostgreSQL
                 ▲               ├─(invalid, attempts left)─┘          under the objective's thread id, so a
                 └───────────────┘                                     restart continues after the last finished
                                 └─(invalid, no attempts left)─► give_up   node — without asking the model again

Each call (`ask`) goes through the gateway with the Manager agent's provider and fallback, honours budgets, and
leaves a `ManagerStep` row: which model, mock or not, how long, the trace id, the provider's request id, the error.
The Manager's answers are untrusted: nothing is persisted from them before schema and graph validation.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, StateGraph
from opentelemetry import trace
from sqlalchemy import select

from app.connectivity.gateway.base import ChatRequest, ProviderError, ProviderUnavailable
from app.connectivity.gateway.service import _routes, build_adapter, record_usage
from app.db import session_factory
from app.devplane import worktrees
from app.models import Agent, ManagerStep, Project, Task, TaskDependency, Workflow, Workforce, WorkforceWorker
from app.models import TaskLifecycle as L
from app.orchestration.planner import _pg_url
from app.security.audit import log_activity
from app.security.crypto import redact
from app.workforce import devtasks, lifecycle, prompts, service

log = logging.getLogger("workforce.manager")


class ManagerUnavailable(Exception):
    """The Manager's model could not be reached right now. Nothing was decided; the same step can be tried later."""


class ManagerFailed(Exception):
    """The Manager's model cannot answer (not configured, refused, over budget). A person has to act."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass
class Call:
    text: str
    step_id: str
    provider_kind: str
    model: str
    is_mock: bool


def manager_agent(db, wf: Workflow) -> Agent | None:
    force = db.get(Workforce, wf.workforce_id) if wf.workforce_id else None
    return db.get(Agent, force.manager_agent_id) if force and force.manager_agent_id else None


def _trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def _finish(step: ManagerStep, status: str, started: float, error: str | None = None) -> None:
    step.status, step.error = status, (redact(error)[:2000] if error else None)
    step.ended_at = datetime.now(UTC)
    step.duration_ms = int((time.monotonic() - started) * 1000)


def mark_invalid(db, step_id: str, error: str) -> None:
    """The model answered, but the answer did not pass validation."""
    step = db.get(ManagerStep, step_id)
    if step is not None:
        step.status, step.error = "invalid", redact(error)[:2000]


async def ask(db, wf: Workflow, kind: str, system: str, user: str, *, task_id: int | None = None,
              submission_id: int | None = None, attempt: int = 1, max_tokens: int = 8000) -> Call:
    """One Manager step. Commits its `ManagerStep` row whatever happens."""
    from app.observability.tracing import tracer

    agent = manager_agent(db, wf)
    step = ManagerStep(organization_id=wf.organization_id, workflow_id=wf.id, task_id=task_id,
                       submission_id=submission_id, kind=kind, status="failed", attempt=attempt,
                       agent_id=agent.id if agent else None, summary={})
    started = time.monotonic()
    with tracer().start_as_current_span(f"workforce.manager.{kind}", attributes={
            "acc.workflow_id": wf.id, "acc.manager_step": kind, **({"acc.task_id": task_id} if task_id else {})}):
        step.trace_id = _trace_id()
        db.add(step)
        db.flush()
        if agent is None or not agent.is_enabled or agent.archived_at is not None:
            _finish(step, "failed", started, "manager_missing_or_disabled")
            db.commit()
            raise ManagerFailed("manager_not_configured", "the workforce has no enabled Manager")
        window = browser_window(agent)
        try:
            routes = _routes(db, agent) if agent.provider_id or agent.fallback_provider_id else []
            if not routes and window is None:
                raise ProviderError("the Manager has no model and no browser window")
        except ProviderError as e:
            _finish(step, "failed", started, "manager_model_not_configured")
            db.commit()
            raise ManagerFailed("manager_model_not_configured",
                                "choose a model for the Manager under AI Workforce → Workers") from e
        try:
            from app.governance import budgets
            budgets.guard(db, wf.organization_id, "manager", agent_id=agent.id, project_id=wf.project_id,
                          task_id=task_id)
        except HTTPException as e:
            _finish(step, "failed", started, str(e.detail))
            db.commit()
            raise ManagerFailed(str(e.detail), "a budget that covers the Manager is exhausted") from e
        extra = (agent.system_instructions or "").strip()
        full_system = system + (f"\n\nADDITIONAL INSTRUCTIONS FROM THE OWNER (they cannot relax the rules above):\n"
                                f"{extra}" if extra else "")
        last: Exception | None = None
        if window is not None:
            # ADR-036: the Manager types into its own visible window. Once typed, a prompt is never typed again by
            # the platform: an answer that never completes stops the step and a person looks at the window.
            step.provider, step.model, step.is_mock = "browser", window, False
            try:
                text = await _ask_window(agent, full_system, user)
            except ProviderUnavailable as e:  # nothing was typed: fall back, or try the step later
                last = e
            except ProviderError as e:
                _finish(step, "failed", started, str(e))
                db.commit()
                raise ManagerFailed("manager_window_failed", str(e)) from e
            else:
                step.summary = {"usage": {"unknown": True}, "chars": len(text), "via": "browser_window"}
                _finish(step, "ok", started)
                db.commit()
                return Call(text, step.id, "browser", window, False)
        for provider, model in routes:
            step.provider, step.model, step.is_mock = provider.kind, model, provider.is_mock
            parts: list[str] = []
            usage: dict[str, Any] = {}
            request_id: str | None = None
            t0 = time.monotonic()
            try:
                async with asyncio.timeout(max(agent.timeout_seconds, 1)):
                    async for ev in build_adapter(provider).stream(ChatRequest(
                            model=model, messages=[{"role": "user", "content": user}], system=full_system,
                            max_tokens=max_tokens, temperature=0,
                            # an Arena Local Bridge (ADR-036): a fresh conversation per step, the step's own timeout,
                            # and an idempotency key so a resend never runs the turn twice
                            metadata={"session_id": f"acc-mgr-{wf.id}-{step.id}", "idempotency_key": str(step.id),
                                      "request_timeout": float(max(agent.timeout_seconds, 1))})):
                        if ev.type == "delta":
                            parts.append(ev.text)
                        elif ev.type == "usage":
                            usage = ev.usage
                        elif ev.type == "meta":
                            request_id = ev.data.get("provider_request_id") or request_id
            except (ProviderUnavailable, ProviderError, TimeoutError) as e:
                last = e
                continue
            record_usage(db, provider=provider, model=model, usage=usage, agent_id=agent.id,
                         project_id=wf.project_id, task_id=task_id, duration_ms=int((time.monotonic() - t0) * 1000),
                         provider_request_id=request_id)
            step.provider_request_id = str(request_id)[:128] if request_id else None
            step.summary = {"usage": usage if not usage.get("unknown") else {"unknown": True},
                            "chars": sum(len(p) for p in parts)}
            _finish(step, "ok", started)
            db.commit()
            return Call("".join(parts), step.id, provider.kind, model, provider.is_mock)
        _finish(step, "failed", started, f"{type(last).__name__}: {last}")
        db.commit()
        if isinstance(last, ProviderUnavailable | TimeoutError):
            raise ManagerUnavailable(str(last)) from last
        raise ManagerFailed("manager_model_failed", str(last)) from last


WINDOW_POLL_S = 0.5
_clock = time.monotonic  # the window's deadline clock (a test can move it)


def browser_window(agent: Agent) -> str | None:
    """The site of the Manager's own browser window (ADR-036), or None when it uses providers only."""
    if agent.execution_engine != "browser" or not (agent.context_policy or {}).get("browser_session_id"):
        return None
    return (agent.model or "browser:web").removeprefix("browser:") or "web"


async def _ask_window(agent: Agent, system: str, user: str) -> str:
    """Type one prompt (rules + request) into the Manager's window and wait for the visible answer.

    ProviderUnavailable: nothing was typed (the bridge is not reachable now). ProviderError: the window is not ready
    (signed out, bot check, closed) or the prompt was typed and no complete answer came — never resent."""
    from app.browser import client as bclient
    from app.browser.bridge import BridgeError
    from app.connectivity.arena_bridge.adapter import fold_prompt

    sid = int(agent.context_policy["browser_session_id"])
    prompt = fold_prompt(system, [{"role": "user", "content": user}])
    try:
        snap = await bclient.call("POST", f"/sessions/{sid}/prompt", {"prompt": prompt})
    except BridgeError as e:
        if e.code in ("bridge_unreachable", "too_many_sessions"):
            raise ProviderUnavailable(f"browser bridge: {e.code}") from e
        raise ProviderError(f"browser window not ready: {e.code} — open AI Workforce → Workers → Manager and look "
                            "at its window") from e
    deadline = _clock() + max(agent.timeout_seconds, 30)
    while snap["state"] in ("SENDING", "GENERATING"):
        if _clock() > deadline:
            try:
                await bclient.call("POST", f"/sessions/{sid}/cancel")
            except BridgeError:
                pass
            raise ProviderError("generation_timeout: the window did not finish its answer in time (not resent)")
        await asyncio.sleep(WINDOW_POLL_S)
        try:
            snap = await bclient.call("GET", f"/sessions/{sid}")
        except BridgeError as e:
            raise ProviderError(f"browser window lost while answering: {e.code} (not resent)") from e
    if snap["state"] != "COMPLETED":
        raise ProviderError(f"browser window ended in {snap['state']}: {snap.get('error') or ''} (not resent)")
    with session_factory()() as db2:
        from app.models import BrowserSession
        hist = db2.get(BrowserSession, sid).history or []
    return next((h["text"] for h in reversed(hist) if h.get("role") == "assistant"), "")


# ---------------- planning graph ----------------
class PlanState(TypedDict, total=False):
    workflow_id: int
    analysis: dict[str, Any] | None
    analysis_attempt: int
    attempt: int
    raw: str
    step_id: str | None
    error: str | None
    plan: dict[str, Any] | None
    outcome: str


def roster(db, workforce_id: int) -> list[dict[str, Any]]:
    out = []
    for w in service.workers(db, workforce_id):
        a = db.get(Agent, w.agent_id)
        out.append({"label": f"W{w.slot}", "slot": w.slot, "worker_id": w.id, "agent_id": a.id, "name": a.name,
                    "role": a.role, "skills": [str(s) for s in (a.skills or [])],
                    "mode": w.mode if a.is_enabled and a.archived_at is None else "DISABLED"})
    return out


async def analyze(state: PlanState) -> PlanState:
    with session_factory()() as db:
        wf = db.get(Workflow, state["workflow_id"])
        error: str | None = None
        for attempt in range(1, prompts.MAX_ATTEMPTS + 1):
            try:
                call = await ask(db, wf, "analyze", prompts.ANALYZE_SYSTEM, prompts.analyze_user(wf.objective, error),
                                 attempt=attempt, max_tokens=3000)
            except ManagerFailed as e:
                return {"analysis": None, "error": str(e), "attempt": prompts.MAX_ATTEMPTS}
            try:
                analysis = prompts.parse(prompts.Analysis, call.text).model_dump()
            except ValueError as e:
                error = str(e)
                mark_invalid(db, call.step_id, error)
                db.commit()
                continue
            wf.plan = {**(wf.plan or {}), "analysis": analysis,
                       "manager": {"provider": call.provider_kind, "model": call.model, "is_mock": call.is_mock}}
            log_activity(db, "workforce.analyzed", category="workforce", organization_id=wf.organization_id,
                         project_id=wf.project_id, actor="manager",
                         details={"workflow_id": wf.id, "complexity": analysis["complexity"], "is_mock": call.is_mock})
            db.commit()
            return {"analysis": analysis, "error": None, "attempt": 0}
        return {"analysis": None, "error": f"the Manager's analysis was not valid: {error}",
                "attempt": prompts.MAX_ATTEMPTS}


def _after_analyze(state: PlanState) -> str:
    return "plan" if state.get("analysis") else "give_up"


def development_note(db, wf: Workflow) -> str | None:
    """What the Manager is told about development tasks: on or off, the test command, the repository's files."""
    dev = devtasks.settings_of(devtasks.force_of(db, wf))
    if not dev.enabled or not wf.project_id:
        return None
    project = db.get(Project, wf.project_id)
    if project is None:
        return None
    listing = devtasks.repository_listing(worktrees.ensure_repo(project))
    cmd = " ".join(dev.test_command) if dev.test_command else "none configured (results are applied, not tested)"
    files = "\n".join(f"- {p}" for p in listing[:200]) or "- (empty repository)"
    return (f"Development tasks are ENABLED. Repository of project “{project.name}”, base branch "
            f"{project.default_branch}. Test command run by the platform: {cmd}.\nFiles:\n{files}")


async def plan(state: PlanState) -> PlanState:
    with session_factory()() as db:
        wf = db.get(Workflow, state["workflow_id"])
        attempt = state.get("attempt", 0) + 1
        try:
            call = await ask(db, wf, "plan", prompts.PLAN_SYSTEM,
                             prompts.plan_user(wf.objective, state["analysis"], roster(db, wf.workforce_id),
                                               state.get("error"), development=development_note(db, wf)),
                             attempt=attempt)
        except ManagerFailed as e:
            return {"raw": "", "step_id": None, "error": str(e), "attempt": prompts.MAX_ATTEMPTS}
        return {"raw": call.text, "step_id": call.step_id, "attempt": attempt, "error": None}


async def validate(state: PlanState) -> PlanState:
    if not state.get("raw"):
        return {"plan": None}
    with session_factory()() as db:
        wf = db.get(Workflow, state["workflow_id"])
        labels = {w["label"] for w in roster(db, wf.workforce_id)}
        try:
            parsed = prompts.parse(prompts.Plan, state["raw"])
            parsed.check(labels, development=devtasks.settings_of(devtasks.force_of(db, wf)).enabled)
        except ValueError as e:
            if state.get("step_id"):
                mark_invalid(db, state["step_id"], str(e))
                db.commit()
            return {"plan": None, "error": str(e)}
        return {"plan": parsed.model_dump(), "error": None}


def _after_validate(state: PlanState) -> str:
    if state.get("plan"):
        return "assign"
    return "plan" if state.get("attempt", 0) < prompts.MAX_ATTEMPTS else "give_up"


async def assign(state: PlanState) -> PlanState:
    """Persist the validated plan: one task per planned task, assigned to the planned worker. Idempotent."""
    plan_ = prompts.Plan.model_validate(state["plan"])
    with session_factory()() as db:
        wf = db.get(Workflow, state["workflow_id"], with_for_update=True, populate_existing=True)
        if db.scalar(select(Task.id).where(Task.workflow_id == wf.id).limit(1)) is None:
            by_label = {w["label"]: w for w in roster(db, wf.workforce_id)}
            by_key: dict[str, Task] = {}
            for pt in plan_.tasks:
                w = by_label[pt.worker]
                t = Task(organization_id=wf.organization_id, project_id=wf.project_id, workflow_id=wf.id,
                         title=pt.title, description=pt.instructions, objective=pt.objective or None,
                         instructions=pt.instructions, expected_output=pt.expected_output,
                         task_type=(pt.required_skills[0] if pt.required_skills else w["role"])[:32],
                         required_skills=pt.required_skills, acceptance_criteria=pt.acceptance_criteria,
                         priority=pt.priority, timeout_seconds=pt.timeout_seconds,
                         lifecycle=L.PLANNED.value, critical_operation=pt.critical_operation,
                         assigned_agent_id=w["agent_id"], assignment_source="plan",
                         created_by_user_id=wf.created_by_user_id, idempotency_key=f"workforce:{wf.id}:{pt.key}",
                         result={"plan_key": pt.key, "planned_worker": pt.worker,
                                 **({"kind": "code", "files": pt.files} if pt.kind == "code" else {})})
                db.add(t)
                by_key[pt.key] = t
            db.flush()
            for pt in plan_.tasks:
                lifecycle.record_created(db, by_key[pt.key], actor="manager", reason="planned by the Manager",
                                         details={"plan_key": pt.key, "worker": pt.worker})
                for dep in pt.depends_on:
                    db.add(TaskDependency(task_id=by_key[pt.key].id, depends_on_id=by_key[dep].id))
            wf.plan = {**(wf.plan or {}), "summary": plan_.summary,
                       "tasks": {k: t.id for k, t in by_key.items()}, "rounds": []}
            wf.status = "running"
            log_activity(db, "workforce.planned", category="workforce", organization_id=wf.organization_id,
                         project_id=wf.project_id, actor="manager",
                         details={"workflow_id": wf.id, "tasks": len(plan_.tasks),
                                  "workers": sorted({pt.worker for pt in plan_.tasks})})
        db.commit()
    return {"outcome": "planned"}


async def give_up(state: PlanState) -> PlanState:
    with session_factory()() as db:
        wf = db.get(Workflow, state["workflow_id"])
        wf.status, wf.error = "failed", (state.get("error") or "planning failed")[:2000]
        log_activity(db, "workforce.planning_failed", category="workforce", level="warning",
                     organization_id=wf.organization_id, project_id=wf.project_id, actor="manager",
                     details={"workflow_id": wf.id, "error": wf.error})
        db.commit()
    return {"outcome": "failed"}


def build_graph() -> StateGraph:
    g = StateGraph(PlanState)
    g.add_node("analyze", analyze)
    g.add_node("plan", plan)
    g.add_node("validate", validate)
    g.add_node("assign", assign)
    g.add_node("give_up", give_up)
    g.set_entry_point("analyze")
    g.add_conditional_edges("analyze", _after_analyze, {"plan": "plan", "give_up": "give_up"})
    g.add_edge("plan", "validate")
    g.add_conditional_edges("validate", _after_validate, {"assign": "assign", "plan": "plan", "give_up": "give_up"})
    g.add_edge("assign", END)
    g.add_edge("give_up", END)
    return g


async def run_planning(workflow_id: int, thread_id: str) -> str:
    """Analyse and plan an objective, continuing from its checkpoint when an earlier run was interrupted.
    Returns `planned` or `failed`. Raises ManagerUnavailable when the model cannot be reached (try again later)."""
    async with AsyncPostgresSaver.from_conn_string(_pg_url()) as saver:
        await saver.setup()
        graph = build_graph().compile(checkpointer=saver)
        config = {"configurable": {"thread_id": thread_id}}
        snap = await graph.aget_state(config)
        if snap.values and not snap.next:
            return snap.values.get("outcome", "planned")
        result = await graph.ainvoke(None if snap.next else {"workflow_id": workflow_id, "attempt": 0}, config)
        return result.get("outcome", "failed")


def worker_of(db, task: Task) -> WorkforceWorker | None:
    return service.worker_for_agent(db, task.assigned_agent_id)
