"""M6 — OR-01 (plan → DAG via LangGraph), OR-02 (selection), OR-03 (fan-out/fan-in, dependency scheduling),
OR-04 (review loop, partial), OR-05 (resume), OR-06 (no duplicate execution). Real PostgreSQL; the model is the
built-in, clearly labelled mock provider."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import func, select

import app.main  # noqa: F401  (registers the real engines; nothing is rebound here)
from app.connectivity.gateway.adapters import mock_plan
from app.models import Agent, AuditLog, Execution, Project, Provider, Task, UsageRecord, Workflow
from app.models.agents import project_agents
from app.orchestration import planner, scheduler
from app.orchestration.planner import parse_plan
from app.orchestration.selection import select_agent
from app.runtime import service


# ---------------- helpers ----------------
def mock_provider(db) -> Provider:
    return db.scalar(select(Provider).where(Provider.organization_id == 1, Provider.kind == "mock"))


def agent(db, role, *, skills=(), tools=(), concurrency=2, priority=5, **kw) -> Agent:
    p = mock_provider(db)
    a = Agent(organization_id=1, name=f"{role} {uuid.uuid4().hex[:5]}", identifier=f"or-{role}-{uuid.uuid4().hex[:6]}",
              role=role, skills=list(skills), state="IDLE", execution_engine="api", provider_id=p.id,
              model="mock-model", tool_permissions=list(tools), concurrency_limit=concurrency, priority=priority,
              timeout_seconds=60, retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1},
              **kw)
    db.add(a)
    db.commit()
    return a


def project(db, agents) -> Project:
    p = Project(organization_id=1, name=f"P {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.flush()
    for a in agents:
        db.execute(project_agents.insert().values(project_id=p.id, agent_id=a.id))
    db.commit()
    return p


def workflow(db, proj, objective) -> Workflow:
    wf = Workflow(organization_id=1, project_id=proj.id, title="wf", objective=objective, status="planning",
                  thread_id=f"workflow:{uuid.uuid4()}")
    db.add(wf)
    db.commit()
    return wf


def tasks_of(db, wf) -> dict[str, Task]:
    db.expire_all()
    return {t.title.removeprefix("[MOCK] "): t for t in db.scalars(select(Task).where(Task.workflow_id == wf.id))}


def run_ready_executions(db, wf) -> list[str]:
    """Do what workers do: run each dispatched execution, then fold the result back into its task."""
    db.expire_all()
    exs = db.scalars(select(Execution).join(Task, Task.id == Execution.task_id).where(
        Task.workflow_id == wf.id, Execution.status == "CREATED")).all()
    for ex in exs:
        asyncio.run(service.run_execution(ex.id))
        asyncio.run(scheduler.on_execution_finished(db, ex.id))
    return [e.id for e in exs]


DIAMOND = """Build the feature:
1. Design API [architect]
2. Build backend [backend] (after 1)
3. Build frontend [frontend] (after 1)
4. Integrate [qa] (after 2, 3)"""


# ---------------- OR-01: plan validation ----------------
def test_plan_validation_rejects_bad_graphs():
    good = json.loads(mock_plan(f"OBJECTIVE:\n{DIAMOND}"))
    assert [t["depends_on"] for t in good["tasks"]] == [[], ["t1"], ["t1"], ["t2", "t3"]]
    assert all(t["title"].startswith("[MOCK]") for t in good["tasks"])  # never passes as a real plan
    parse_plan("Sure! here it is:\n" + json.dumps(good) + "\nanything else?")  # prose around JSON is tolerated
    bad = [
        ({"tasks": [{"key": "a", "title": "x", "acceptance_criteria": ["c"], "depends_on": ["b"]},
                    {"key": "b", "title": "y", "acceptance_criteria": ["c"], "depends_on": ["a"]}]}, "cycle"),
        ({"tasks": [{"key": "a", "title": "x", "acceptance_criteria": ["c"], "depends_on": ["zz"]}]}, "unknown"),
        ({"tasks": [{"key": "a", "title": "x", "acceptance_criteria": []}]}, "schema"),
        ({"tasks": [{"key": f"k{i}", "title": "x", "acceptance_criteria": ["c"]} for i in range(26)]}, "schema"),
        ({"tasks": [{"key": "a", "title": "x", "acceptance_criteria": ["c"]}] * 2}, "duplicate"),
    ]
    for plan, why in bad:
        with pytest.raises(ValueError, match=why):
            parse_plan(json.dumps(plan))
    with pytest.raises(ValueError, match="no JSON"):
        parse_plan("I cannot help with that")


def test_langgraph_planner_creates_task_dag_and_resumes_from_checkpoint(db):
    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    proj = project(db, [pm])
    wf = workflow(db, proj, DIAMOND)

    def planner_calls():
        return db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.agent_id == pm.id))

    async def interrupted():
        # Simulate a crash right after the model replied and the plan was validated, before tasks were written.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        async with AsyncPostgresSaver.from_conn_string(planner._pg_url()) as saver:
            await saver.setup()
            g = planner.build_graph().compile(checkpointer=saver, interrupt_after=["validate"])
            await g.ainvoke({"workflow_id": wf.id, "attempt": 0}, {"configurable": {"thread_id": wf.thread_id}})
    asyncio.run(interrupted())
    assert planner_calls() == 1 and not tasks_of(db, wf)
    assert asyncio.run(planner.run_planning(wf.id, wf.thread_id)) == "planned"  # resumes at 'persist'
    assert planner_calls() == 1  # the model was not asked again
    assert asyncio.run(planner.run_planning(wf.id, wf.thread_id)) == "planned"  # finished thread: no-op
    ts = tasks_of(db, wf)
    assert set(ts) == {"Design API", "Build backend", "Build frontend", "Integrate"}
    assert all(t.status == "PLANNED" and t.acceptance_criteria for t in ts.values())
    db.refresh(wf)
    assert wf.status == "running" and wf.plan["planner"]["is_mock"] is True
    from app.models import TaskDependency
    edges = {(d.task_id, d.depends_on_id) for d in db.scalars(select(TaskDependency).where(
        TaskDependency.task_id.in_([t.id for t in ts.values()])))}
    assert edges == {(ts["Build backend"].id, ts["Design API"].id), (ts["Build frontend"].id, ts["Design API"].id),
                     (ts["Integrate"].id, ts["Build backend"].id), (ts["Integrate"].id, ts["Build frontend"].id)}


def test_invalid_plans_are_retried_with_feedback_then_fail_the_workflow(db):
    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    wf = workflow(db, project(db, [pm]), "1. A (after 2)\n2. B (after 1)")
    assert asyncio.run(planner.run_planning(wf.id, wf.thread_id)) == "failed"
    db.refresh(wf)
    assert wf.status == "failed" and "cycle" in wf.error and not tasks_of(db, wf)
    assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.agent_id == pm.id)) == 3


# ---------------- OR-02: selection ----------------
def test_agent_selection_filters_then_scores(db):
    be1, be2 = agent(db, "backend", skills=["python"]), agent(db, "backend", skills=["python", "postgres"])
    fe = agent(db, "frontend")
    outsider = agent(db, "backend", skills=["python", "postgres"], priority=1)  # not on the roster
    proj = project(db, [be1, be2, fe])
    t = Task(organization_id=1, project_id=proj.id, title="x", task_type="backend", required_skills=["postgres"])
    db.add(t)
    db.commit()
    s = select_agent(db, t)
    assert s.agent.id == be2.id  # only be2 has the skill; outsider is not on the project
    assert outsider.id not in [c["agent_id"] for c in s.candidates]
    t.required_skills = ["python"]
    # history: be1 has failures, be2 successes → be2 preferred
    for a, status_ in ((be1, "FAILED"), (be2, "COMPLETED")):
        for _ in range(3):
            db.add(Execution(organization_id=1, agent_id=a.id, engine="api", status=status_, input_context={}))
    db.commit()
    assert select_agent(db, t).agent.id == be2.id
    # workload: be2 fully busy → be1
    be2.concurrency_limit = 1
    db.add(Execution(organization_id=1, agent_id=be2.id, engine="api", status="RUNNING", input_context={}))
    db.commit()
    assert select_agent(db, t).agent.id == be1.id
    be1.is_enabled = False
    db.commit()
    assert select_agent(db, t).reason == "all_capable_agents_busy"
    t.required_skills = ["rust"]
    assert select_agent(db, t).reason == "no_agent_with_required_skills"


# ---------------- OR-03/04/06: scheduling ----------------
def planned_diamond(db):
    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    crew = [agent(db, r) for r in ("architect", "backend", "frontend", "qa")]
    wf = workflow(db, project(db, [pm, *crew]), DIAMOND)
    assert asyncio.run(planner.run_planning(wf.id, wf.thread_id)) == "planned"
    return wf, crew


def approve_all_in_review(db, wf):
    for t in tasks_of(db, wf).values():
        if t.status == "REVIEW":
            scheduler.review(db, t, "approve", "ok", "user:1")


def test_fan_out_fan_in_with_review_gate(db):
    wf, crew = planned_diamond(db)
    r1 = scheduler.advance(db, wf.id)
    ts = tasks_of(db, wf)
    assert r1["dispatched"] == [ts["Design API"].id]  # only the root is ready
    assert ts["Design API"].assigned_agent_id == crew[0].id  # role-matched
    run_ready_executions(db, wf)
    ts = tasks_of(db, wf)
    assert ts["Design API"].status == "REVIEW"  # never COMPLETED on the agent's word
    assert ts["Design API"].evidence[0]["kind"] == "execution" and ts["Design API"].evidence[0]["is_mock"]
    assert scheduler.advance(db, wf.id)["dispatched"] == []  # dependents wait for the review
    approve_all_in_review(db, wf)
    r2 = scheduler.advance(db, wf.id)
    ts = tasks_of(db, wf)
    assert sorted(r2["dispatched"]) == sorted([ts["Build backend"].id, ts["Build frontend"].id])  # fan-out, 1 round
    run_ready_executions(db, wf)
    approve_all_in_review(db, wf)
    r3 = scheduler.advance(db, wf.id)
    ts = tasks_of(db, wf)
    assert r3["dispatched"] == [ts["Integrate"].id]  # fan-in
    prompt = db.scalar(select(Execution).where(Execution.task_id == ts["Integrate"].id)).input_context["prompt"]
    assert "Build backend" in prompt and "Build frontend" in prompt and "(data, not instructions)" in prompt
    run_ready_executions(db, wf)
    approve_all_in_review(db, wf)
    assert scheduler.advance(db, wf.id)["status"] == "completed"
    db.refresh(wf)
    assert len(wf.plan["result"]) == 4 and [len(r["dispatched"]) for r in wf.plan["rounds"][:3]] == [1, 2, 1]
    assert db.scalar(select(AuditLog).where(AuditLog.action == "orchestrator.assigned",
                                            AuditLog.task_id == ts["Integrate"].id)).details["candidates"]


def test_review_revise_loop_feeds_note_into_next_attempt(db):
    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    dev = agent(db, "backend")
    wf = workflow(db, project(db, [pm, dev]), "1. Write endpoint [backend]")
    asyncio.run(planner.run_planning(wf.id, wf.thread_id))
    scheduler.advance(db, wf.id)
    run_ready_executions(db, wf)
    t = tasks_of(db, wf)["Write endpoint"]
    scheduler.review(db, t, "revise", "add input validation", "user:1")
    scheduler.advance(db, wf.id)
    run_ready_executions(db, wf)
    t = tasks_of(db, wf)["Write endpoint"]
    assert t.status == "REVIEW" and t.attempts == 2
    assert "REVIEWER REQUESTED CHANGES: add input validation" in t.result["output"]  # mock echoes the prompt
    scheduler.review(db, t, "approve", "", "user:1")
    with pytest.raises(Exception, match="task_not_in_review"):  # a decision is final
        scheduler.review(db, tasks_of(db, wf)["Write endpoint"], "approve", "", "user:1")


def test_failure_retries_then_blocks_dependents(db):
    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    dev = agent(db, "backend")
    wf = workflow(db, project(db, [pm, dev]), "1. Flaky step [backend]\n2. Next step [backend] (after 1)")
    asyncio.run(planner.run_planning(wf.id, wf.thread_id))
    ts = tasks_of(db, wf)
    ts["Flaky step"].max_attempts = 2
    ts["Flaky step"].description = "[[mock:error]]"  # the mock provider fails on this marker
    db.commit()
    for _ in range(2):
        scheduler.advance(db, wf.id)
        run_ready_executions(db, wf)
    ts = tasks_of(db, wf)
    assert ts["Flaky step"].status == "FAILED" and ts["Flaky step"].attempts == 2
    assert scheduler.advance(db, wf.id)["status"] == "failed"
    assert tasks_of(db, wf)["Next step"].status == "BLOCKED"


def test_duplicate_events_and_advances_never_run_work_twice(db):
    wf, _ = planned_diamond(db)
    scheduler.advance(db, wf.id)
    scheduler.advance(db, wf.id)  # duplicate advance: nothing new
    root = tasks_of(db, wf)["Design API"]
    assert db.scalar(select(func.count()).select_from(Execution).where(Execution.task_id == root.id)) == 1
    ex_id = run_ready_executions(db, wf)[0]
    assert asyncio.run(scheduler.on_execution_finished(db, ex_id)) == "ignored"  # duplicate execution.finished
    assert tasks_of(db, wf)["Design API"].attempts == 1


def test_orchestration_api_permissions_and_idempotent_submission(admin_client, client, db, make_user):
    from tests.conftest import login_as

    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    p = admin_client.post("/api/projects", json={"name": f"API {uuid.uuid4().hex[:6]}", "agent_ids": [pm.id]})
    assert p.status_code == 201 and p.json()["agent_ids"] == [pm.id]
    body = {"project_id": p.json()["id"], "title": "t", "objective": "1. x", "idempotency_key": "obj-1"}
    w1, w2 = admin_client.post("/api/workflows", json=body).json(), admin_client.post("/api/workflows", json=body).json()
    assert w1["id"] == w2["id"] and w1["status"] == "planning"
    assert admin_client.get(f"/api/workflows/{w1['id']}").json()["tasks"] == []
    make_user("viewer-or", "VIEWER")
    client.headers.pop("x-csrf-token", None)
    v = login_as(client, "viewer-or", "user-password-123")
    assert v.post("/api/workflows", json={**body, "idempotency_key": "obj-2"}).status_code == 403
    assert v.get(f"/api/workflows/{w1['id']}").status_code == 200


# ---------------- EV-01 / OR-08: evaluation gate ----------------
def test_evaluator_is_deterministic_and_reports_each_criterion(db):
    from app.evaluation.engine import criterion_coverage, evaluate

    assert criterion_coverage("Endpoint validates input", "The endpoint now validates every input field") == 1.0
    assert criterion_coverage("Returns pagination metadata", "hello world") == 0.0
    dev = agent(db, "backend")
    t = Task(organization_id=1, title="x", acceptance_criteria=["Endpoint validates input", "Adds pagination metadata"])
    db.add(t)
    db.flush()
    ex = Execution(organization_id=1, agent_id=dev.id, task_id=t.id, engine="api", status="COMPLETED",
                   input_context={}, output={"text": "The endpoint validates input."})
    db.add(ex)
    db.commit()
    ev = asyncio.run(evaluate(db, t, ex))
    assert ev.decision == "revise"  # second criterion has no evidence
    crit = [c for c in ev.checks if c["check"] == "acceptance_criterion"]
    assert [c["passed"] for c in crit] == [True, False]
    assert ev.scores["test_pass_rate"] is None and ev.scores["security_findings"] is None  # not guessed
    ex.output = {"text": ""}
    assert asyncio.run(evaluate(db, t, ex)).decision == "reject"


def test_reviewer_model_can_withhold_but_never_grant_approval(db):
    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    dev = agent(db, "backend")
    rev = agent(db, "code_reviewer", tools=["pr.review"], skills=["review"])
    wf = workflow(db, project(db, [pm, dev, rev]), "1. Build it [backend]")
    asyncio.run(planner.run_planning(wf.id, wf.thread_id))
    t = tasks_of(db, wf)["Build it"]
    t.description, t.max_attempts = "[[mock:review-fail]]", 2  # the mock reviewer rejects every criterion
    db.commit()
    for _ in range(2):
        scheduler.advance(db, wf.id)
        run_ready_executions(db, wf)
    t = tasks_of(db, wf)["Build it"]
    assert t.status == "FAILED" and "evaluation did not pass" in t.error
    assert any("EVALUATOR: reviewer: criterion 1 not met" in n for n in t.result["review_notes"])
    with pytest.raises(Exception, match="task_not_in_review"):
        scheduler.review(db, t, "approve", "", "user:1")


def test_human_cannot_approve_without_a_passing_evaluation(db):
    wf, _ = planned_diamond(db)
    scheduler.advance(db, wf.id)
    run_ready_executions(db, wf)
    t = tasks_of(db, wf)["Design API"]
    assert t.status == "REVIEW" and t.evidence[-1]["kind"] == "evaluation" and t.evidence[-1]["decision"] == "approve"
    from app.models import EvaluationResult
    ev = db.get(EvaluationResult, t.result["evaluation_id"])
    ev.decision = "revise"  # simulate a failed evaluation reaching review
    db.commit()
    with pytest.raises(Exception, match="evaluation_not_passed"):
        scheduler.review(db, t, "approve", "", "user:1")


# ---------------- OR-04: approval stage for critical operations ----------------
def test_critical_task_waits_for_approval_while_independent_work_continues(db, make_user):
    from app.governance import approvals
    from app.models import Approval

    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    ops, dev = agent(db, "devops"), agent(db, "backend")
    wf = workflow(db, project(db, [pm, ops, dev]),
                  "1. Deploy service [devops] {deploy.production}\n2. Write docs [backend]\n3. Announce [backend] (after 1)")
    asyncio.run(planner.run_planning(wf.id, wf.thread_id))
    ts = tasks_of(db, wf)
    assert ts["Deploy service"].critical_operation == "deploy.production"
    scheduler.advance(db, wf.id)
    run_ready_executions(db, wf)
    approve_all_in_review(db, wf)
    ts = tasks_of(db, wf)
    assert ts["Deploy service"].status == "APPROVAL" and ts["Write docs"].status == "COMPLETED"
    a = db.get(Approval, ts["Deploy service"].result["approval_id"])
    assert (a.status, a.operation, a.risk_level, a.task_id) == ("pending", "deploy.production", "critical",
                                                                ts["Deploy service"].id)
    assert scheduler.advance(db, wf.id)["dispatched"] == []  # the dependent waits; nothing else is stuck
    assert tasks_of(db, wf)["Announce"].status == "PLANNED"
    approver = make_user("approver-or", "SUPER_ADMIN")
    approvals.decide(db, a, user_id=approver.id, decision="approve", reason="")
    db.commit()
    assert tasks_of(db, wf)["Deploy service"].status == "COMPLETED"
    assert scheduler.advance(db, wf.id)["dispatched"] == [tasks_of(db, wf)["Announce"].id]


def test_rejected_approval_fails_the_task_and_blocks_dependents(db, make_user):
    from app.governance import approvals
    from app.models import Approval

    pm = agent(db, "project_manager", tools=["tasks.plan"], priority=1)
    ops = agent(db, "devops")
    wf = workflow(db, project(db, [pm, ops]), "1. Drop table [devops] {db.destructive}\n2. Reindex [devops] (after 1)")
    asyncio.run(planner.run_planning(wf.id, wf.thread_id))
    scheduler.advance(db, wf.id)
    run_ready_executions(db, wf)
    approve_all_in_review(db, wf)
    t = tasks_of(db, wf)["Drop table"]
    a = db.get(Approval, t.result["approval_id"])
    approvals.decide(db, a, user_id=make_user("approver-rej", "SUPER_ADMIN").id, decision="reject",
                     reason="not during business hours")
    db.commit()
    assert tasks_of(db, wf)["Drop table"].status == "FAILED"
    assert scheduler.advance(db, wf.id)["status"] == "failed"
    assert tasks_of(db, wf)["Reindex"].status == "BLOCKED"
