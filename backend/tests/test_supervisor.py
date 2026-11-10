"""M7 — Agent Supervisor: detection (SV-02/04), reversible actions (SV-03), never above policy (SV-05), API (SV-06)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

import app.main  # noqa: F401
from app.config import get_settings
from app.governance import approvals
from app.models import (
    Agent,
    Execution,
    ExecutionEvent,
    OutboxMessage,
    Project,
    SupervisorFinding,
    Task,
    TaskDependency,
    Workflow,
    Worktree,
)
from app.runtime import circuit
from app.security.audit import log_activity
from app.supervisor import engine


def _agent(db, **kw) -> Agent:
    kw.setdefault("concurrency_limit", 4)
    a = Agent(organization_id=1, name=f"S {uuid.uuid4().hex[:5]}", identifier=f"sv-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", **kw)
    db.add(a)
    db.commit()
    return a


def _ex(db, agent, status="RUNNING", silent_for=0, **kw) -> Execution:
    ex = Execution(organization_id=1, agent_id=agent.id, engine="api", status=status, input_context={}, **kw)
    db.add(ex)
    db.flush()
    db.add(ExecutionEvent(execution_id=ex.id, seq=1, type="state", payload={"to": status},
                          ts=datetime.now(UTC) - timedelta(seconds=silent_for)))
    db.commit()
    return ex


def finding(db, kind, subject_id):
    return db.scalar(select(SupervisorFinding).where(SupervisorFinding.kind == kind,
                                                     SupervisorFinding.subject_id == str(subject_id)))


def test_stalled_execution_is_resumed_through_the_bus(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "execution_dispatch", "bus")
    ex = _ex(db, _agent(db), silent_for=get_settings().supervisor_stall_seconds + 60)
    fresh = _ex(db, _agent(db))
    engine.sweep(db, 1)
    f = finding(db, "stalled_execution", ex.id)
    assert f.status == "actioned" and f.action["action"] == "resume_stalled"
    assert finding(db, "stalled_execution", fresh.id) is None
    assert db.scalar(select(OutboxMessage).where(OutboxMessage.envelope["correlation_id"].astext == ex.id,
                                                 OutboxMessage.envelope["payload"]["reason"].astext.like("resume:supervisor%")))
    engine.sweep(db, 1)  # still stalled: same finding, no second action
    assert db.scalar(select(SupervisorFinding).where(SupervisorFinding.kind == "stalled_execution",
                                                     SupervisorFinding.subject_id == ex.id)).id == f.id
    assert len(db.scalars(select(OutboxMessage).where(
        OutboxMessage.envelope["correlation_id"].astext == ex.id)).all()) == 1


def test_runaway_execution_is_stopped(db):
    ex = _ex(db, _agent(db, token_budget=1000), input_tokens=900, output_tokens=500)
    engine.sweep(db, 1)
    db.refresh(ex)
    assert ex.cancel_requested and finding(db, "runaway_execution", ex.id).action["action"] == "stop_runaway"


def test_repeated_failures_isolate_the_agent_until_released(db, admin_client):
    a = _agent(db)
    for _ in range(3):
        _ex(db, a, status="FAILED", ended_at=datetime.now(UTC), error_category="transient")
    engine.sweep(db, 1)
    assert circuit.state(db, "agent", a.id) == "open"
    assert finding(db, "repeated_failures", a.id).action["action"] == "isolate_agent"
    assert admin_client.post(f"/api/supervisor/agents/{a.id}/release").json()["circuit"] == "closed"
    db.expire_all()
    assert circuit.state(db, "agent", a.id) == "closed"


def test_policy_denials_are_suspicious_and_reduce_concurrency(db):
    a = _agent(db, concurrency_limit=4)
    for _ in range(3):
        log_activity(db, "runtime.tool_denied", category="security", organization_id=1, agent_id=a.id)
    db.commit()
    engine.sweep(db, 1)
    db.refresh(a)
    assert a.concurrency_limit == 2 and finding(db, "suspicious_tool_usage", a.id).action["from"] == 4


def test_deadlock_duplicates_conflicts_and_idle_agents_are_detected(db):
    p = Project(organization_id=1, name=f"SV {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.flush()
    wf = Workflow(organization_id=1, project_id=p.id, title="w", objective="o", status="running",
                  thread_id=f"sv:{uuid.uuid4()}")
    db.add(wf)
    db.flush()
    blocked = Task(organization_id=1, project_id=p.id, workflow_id=wf.id, title="a", status="BLOCKED")
    waiting = Task(organization_id=1, project_id=p.id, workflow_id=wf.id, title="b", status="PLANNED")
    title = f"Build login {uuid.uuid4().hex[:4]}"
    d1 = Task(organization_id=1, project_id=p.id, title=title, status="READY", required_skills=["backend"])
    d2 = Task(organization_id=1, project_id=p.id, title=f"[MOCK] {title}!", status="IN_PROGRESS")
    db.add_all([blocked, waiting, d1, d2])
    db.flush()
    db.add(TaskDependency(task_id=waiting.id, depends_on_id=blocked.id))
    wt = Worktree(organization_id=1, project_id=p.id, task_id=d2.id, branch=f"agent/x/{uuid.uuid4().hex[:5]}",
                  base_branch="main", base_commit="abc", path="/tmp/none", status="active",
                  conflicts=[{"task_id": d1.id, "files": ["auth.py"]}])
    db.add(wt)
    db.commit()
    idle = _agent(db)
    engine.sweep(db, 1)
    assert finding(db, "deadlock", wf.id).details["planned"] == [waiting.id]
    assert finding(db, "duplicate_work", d2.id).details["duplicate_of"] == d1.id
    assert finding(db, "conflicting_changes", wt.id).details["conflicts"][0]["files"] == ["auth.py"]
    assert d1.id in finding(db, "idle_agent_with_waiting_work", idle.id).details["ready_tasks"]
    for k in ("deadlock", "duplicate_work", "conflicting_changes"):
        f = db.scalar(select(SupervisorFinding).where(SupervisorFinding.kind == k).order_by(SupervisorFinding.id.desc()))
        assert f.action is None  # advisory only: these need a human or the orchestrator
    d2.status = "CANCELLED"
    db.commit()
    engine.sweep(db, 1)
    assert finding(db, "duplicate_work", d2.id).status == "resolved"  # problem gone → finding resolves itself


def test_transient_task_failure_is_retried_once_eligible(db):
    a = _agent(db)
    t = Task(organization_id=1, title=f"flaky {uuid.uuid4().hex[:4]}", status="FAILED", attempts=1, max_attempts=3)
    db.add(t)
    db.flush()
    _ex(db, a, status="FAILED", task_id=t.id, error_category="transient", ended_at=datetime.now(UTC))
    spent = Task(organization_id=1, title=f"spent {uuid.uuid4().hex[:4]}", status="FAILED", attempts=3, max_attempts=3)
    db.add(spent)
    db.commit()
    engine.sweep(db, 1)
    db.refresh(t)
    db.refresh(spent)
    assert t.status == "READY" and spent.status == "FAILED"  # retry budget respected


def test_supervisor_never_overrides_approvals_or_touches_tasks_awaiting_humans(db, monkeypatch):
    """SV-05: a runaway/stalled execution of a task awaiting approval is reported, not acted on; approvals untouched."""
    a = _agent(db, token_budget=10)
    t = Task(organization_id=1, title="deploy", status="APPROVAL", critical_operation="deploy.production")
    db.add(t)
    db.flush()
    ap = approvals.request_approval(db, organization_id=1, operation="deploy.production", title="deploy",
                                    requested_by="orchestrator", payload={"task_id": t.id}, task_id=t.id)
    db.commit()
    ex = _ex(db, a, task_id=t.id, input_tokens=100, silent_for=10_000)
    engine.sweep(db, 1)
    db.refresh(ex)
    db.refresh(t)
    db.refresh(ap)
    assert not ex.cancel_requested and t.status == "APPROVAL" and ap.status == "pending"
    assert finding(db, "runaway_execution", ex.id).action is None
    src = Path(engine.__file__).read_text()
    assert "approvals" not in src.split('"""', 2)[2] and "decide(" not in src  # no code path to approvals
    assert engine.ALLOWED_ACTIONS.isdisjoint({"approve", "merge", "grant_permission", "decide"})


def test_actions_can_be_made_advisory_only(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "supervisor_actions", "")
    ex = _ex(db, _agent(db, token_budget=1), input_tokens=50)
    engine.sweep(db, 1)
    db.refresh(ex)
    assert not ex.cancel_requested and finding(db, "runaway_execution", ex.id).status == "open"


def test_supervisor_api(admin_client, client, db, make_user):
    from tests.conftest import login_as
    assert admin_client.post("/api/supervisor/run").status_code == 200
    h = admin_client.get("/api/supervisor/health").json()
    assert "autonomous_actions" in h and h["last_sweep_at"]
    fs = admin_client.get("/api/supervisor/findings?status=open").json()
    assert all(f["status"] != "resolved" for f in fs)
    if fs:
        r = admin_client.post(f"/api/supervisor/findings/{fs[0]['id']}/resolve").json()
        assert r["status"] == "resolved"
    assert isinstance(admin_client.get("/api/supervisor/actions").json(), list)
    make_user("viewer-sv", "VIEWER")
    client.headers.pop("x-csrf-token", None)
    v = login_as(client, "viewer-sv", "user-password-123")
    assert v.get("/api/supervisor/health").status_code == 200
    assert v.post("/api/supervisor/run").status_code == 403
