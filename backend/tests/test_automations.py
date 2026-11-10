"""UX-06 — in-app automation & scheduling: validation, schedule/event/manual runs, pause/resume/edit/delete, history,
permission re-check at run time."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import app.main  # noqa: F401
from app.automation import engine
from app.models import Automation, AutomationRun, Workflow
from app.security.audit import log_activity


def _project(admin_client) -> dict:
    return admin_client.post("/api/projects", json={"name": f"Auto {uuid.uuid4().hex[:6]}"}).json()


WF = {"type": "workflow", "title": "Nightly review", "objective": "Review yesterday's changes"}


def test_validation(admin_client):
    p = _project(admin_client)
    bad = [({"type": "cron", "expr": "* * * * *"}, WF), ({"type": "cron", "expr": "nope"}, WF),
           ({"type": "interval", "minutes": 1}, WF), ({"type": "event", "action": "automation.run_succeeded"}, WF),
           ({"type": "shell", "cmd": "rm"}, WF), ({"type": "interval", "minutes": 60}, {"type": "exec"}),
           ({"type": "interval", "minutes": 60}, {"type": "workflow", "title": "x"})]
    for trig, act in bad:
        r = admin_client.post("/api/automations", json={"name": "x", "project_id": p["id"], "trigger": trig,
                                                        "action": act})
        assert r.status_code == 422, (trig, act, r.text)


def test_schedule_runs_when_due_and_records_history(admin_client, db):
    p = _project(admin_client)
    a = admin_client.post("/api/automations", json={"name": "nightly", "project_id": p["id"],
                                                    "trigger": {"type": "cron", "expr": "0 3 * * *"},
                                                    "action": WF}).json()
    assert a["status"] == "active" and a["next_run_at"]
    row = db.get(Automation, a["id"])
    assert engine.tick(db, datetime.now(UTC)) == []  # not due yet
    row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    ran = engine.tick(db)
    db.commit()
    assert len(ran) == 1
    run = db.get(AutomationRun, ran[0])
    assert run.status == "succeeded" and run.trigger == "schedule"
    wf = db.get(Workflow, run.result["workflow_id"])
    assert wf.objective == WF["objective"] and wf.project_id == p["id"]
    db.refresh(row)
    assert row.next_run_at > datetime.now(UTC) and row.last_run_at
    hist = admin_client.get(f"/api/automations/{a['id']}/runs").json()
    assert hist[0]["id"] == run.id and hist[0]["status"] == "succeeded"


def test_pause_resume_edit_delete_and_manual_run(admin_client, db):
    p = _project(admin_client)
    a = admin_client.post("/api/automations", json={"name": "hourly", "project_id": p["id"],
                                                    "trigger": {"type": "interval", "minutes": 60},
                                                    "action": WF}).json()
    assert admin_client.post(f"/api/automations/{a['id']}/pause").json()["status"] == "paused"
    row = db.get(Automation, a["id"])
    row.next_run_at = datetime.now(UTC) - timedelta(hours=5)
    db.commit()
    assert a["id"] not in [db.get(AutomationRun, r).automation_id for r in engine.tick(db)]  # paused never runs
    db.commit()
    resumed = admin_client.post(f"/api/automations/{a['id']}/resume").json()
    assert resumed["status"] == "active"
    assert datetime.fromisoformat(resumed["next_run_at"]) > datetime.now(UTC)  # no catch-up storm
    e = admin_client.patch(f"/api/automations/{a['id']}", json={"name": "renamed",
                                                                  "trigger": {"type": "interval", "minutes": 30}})
    assert e.json()["name"] == "renamed" and e.json()["trigger"]["minutes"] == 30
    run = admin_client.post(f"/api/automations/{a['id']}/run").json()
    assert run["trigger"] == "manual" and run["status"] == "succeeded"
    assert admin_client.delete(f"/api/automations/{a['id']}").status_code == 204
    assert admin_client.get(f"/api/automations/{a['id']}/runs").status_code == 404
    acts = set(db.scalars(select(engine.AuditLog.action).where(engine.AuditLog.category == "automation")))
    assert {"automation.created", "automation.paused", "automation.resumed", "automation.updated",
            "automation.run_succeeded", "automation.deleted"} <= acts


def test_event_trigger_runs_once_per_event_and_never_replays(admin_client, db):
    p = _project(admin_client)
    log_activity(db, "github.pr_merged", category="devplane", organization_id=1, project_id=p["id"])  # before
    db.commit()
    a = admin_client.post("/api/automations", json={"name": "after merge", "project_id": p["id"],
                                                    "trigger": {"type": "event", "action": "github.pr_merged"},
                                                    "action": WF}).json()
    engine.tick(db)  # arms the cursor at "now"
    db.commit()
    assert engine.tick(db) == []
    db.commit()
    for _ in range(2):
        log_activity(db, "github.pr_merged", category="devplane", organization_id=1, project_id=p["id"])
    log_activity(db, "github.pr_merged", category="devplane", organization_id=1, project_id=None)  # other scope
    db.commit()
    mine = [r for r in engine.tick(db) if db.get(AutomationRun, r).automation_id == a["id"]]
    db.commit()
    assert len(mine) == 2
    assert [r for r in engine.tick(db) if db.get(AutomationRun, r).automation_id == a["id"]] == []


def test_creator_permissions_rechecked_and_rbac(admin_client, client, db, make_user):
    from tests.conftest import login_as
    p = _project(admin_client)
    a = admin_client.post("/api/automations", json={"name": "x", "project_id": p["id"],
                                                    "trigger": {"type": "interval", "minutes": 60},
                                                    "action": WF}).json()
    viewer = make_user("auto-viewer", "VIEWER")
    row = db.get(Automation, a["id"])
    row.created_by_user_id = viewer.id
    db.commit()
    r = engine.run_once(db, row, "manual")
    db.commit()
    assert r.status == "skipped" and "workflows.run" in r.detail
    login_as(client, "auto-viewer", "user-password-123")
    assert client.get("/api/automations").status_code == 200
    assert client.post(f"/api/automations/{a['id']}/pause").status_code == 403


def test_analyze_project_action_fails_cleanly_without_a_repository(admin_client, db):
    p = _project(admin_client)
    a = admin_client.post("/api/automations", json={"name": "scan", "project_id": p["id"],
                                                    "trigger": {"type": "interval", "minutes": 1440},
                                                    "action": {"type": "analyze_project"}}).json()
    run = admin_client.post(f"/api/automations/{a['id']}/run").json()
    assert run["status"] == "failed" and "no repository" in run["detail"]
