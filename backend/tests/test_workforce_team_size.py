"""ADR-036 — the team can grow to 20 workers and shrink again; an archived worker keeps its history.

Tier: mock (the built-in mock Manager; nothing real is called)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import Agent, Task
from app.workforce import control, manager, service
from tests import wf as h

W = "/api/workforce/workers"


def _cleanup(db):
    """Archive every worker beyond the default four (left from an earlier test)."""
    for w in service.workers(db, h.force_of(db).id):
        if w.slot > 4:
            db.get(Agent, w.agent_id).archived_at = datetime.now(UTC)
    db.commit()


def test_add_workers_up_to_twenty_and_no_more(db, admin_client):
    h.reset(db)
    _cleanup(db)
    try:
        added = []
        for i in range(16):
            r = admin_client.post(W, json={"name": f"Extra {i}", "role": "research" if i % 2 else "translation"})
            assert r.status_code == 201, r.text
            added.append(r.json())
        new = added[0]
        # a new worker is set up like the default four: Human-Assisted, nothing configured, nothing sent anywhere
        assert new["mode"] == "HUMAN_ASSISTED" and new["provider"] is None and new["slot"] > 4
        assert added[1]["skills"] == ["research", "analysis", "requirements"]  # a known specialty brings its skills
        assert added[0]["skills"] == ["translation"]                           # a new one is its own skill
        force = admin_client.get("/api/workforce").json()["workforce"]
        assert len(force["workers"]) == 20 and force["max_workers"] == 20
        r = admin_client.post(W, json={"name": "One too many", "role": "qa"})
        assert r.status_code == 422 and r.json()["detail"] == "too_many_workers"
        # the Manager sees every worker that is not archived
        assert len(manager.roster(db, h.force_of(db).id)) == 20
    finally:
        _cleanup(db)


def test_archiving_a_worker_keeps_its_history_and_frees_a_place(db, admin_client):
    h.reset(db)
    _cleanup(db)
    r = admin_client.post(W, json={"name": "Temp", "role": "qa"}).json()
    wid = r["id"]
    # busy: a task in hand blocks archiving
    wf = h.objective(db, f"1. Check it [qa] @W{r['slot']}")
    assert asyncio.run(control.plan(wf.id)) == "planned"
    h.tick(wf)
    t = db.scalars(select(Task).where(Task.workflow_id == wf.id)).first()
    assert t.assigned_agent_id == r["agent_id"]  # the Manager gave it to the new worker
    busy = admin_client.delete(f"{W}/{wid}")
    assert busy.status_code == 409 and busy.json()["detail"] == "worker_busy"
    t.lifecycle = "CANCELLED"  # the task is out of its hands
    db.commit()
    assert admin_client.delete(f"{W}/{wid}").status_code == 204
    force = admin_client.get("/api/workforce").json()["workforce"]
    assert wid not in [w["id"] for w in force["workers"]]
    db.expire_all()
    assert db.get(Agent, r["agent_id"]).archived_at is not None  # kept, not deleted
    assert t.id is not None and db.get(Task, t.id) is not None     # its tasks stay in the history
    _cleanup(db)


def test_a_disabled_worker_takes_no_work_so_four_of_twenty_can_be_used(db):
    h.reset(db)
    force = h.force_of(db)
    for slot in (2, 3, 4):
        service.update_worker(db, h.worker(db, slot), user=h.admin(db), patch={"mode": "DISABLED"})
    db.commit()
    modes = {w["label"]: w["mode"] for w in manager.roster(db, force.id)}
    assert modes["W1"] == "HUMAN_ASSISTED" and {modes[f"W{i}"] for i in (2, 3, 4)} == {"DISABLED"}
