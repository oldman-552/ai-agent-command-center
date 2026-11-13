"""The Manager runs on the model chosen for it (a provider, here the MOCK) when it has no browser window.

Regression: ADR-038 left the browser window as the Manager's only path, so a Manager set to a provider failed every
objective with `manager_no_browser_window`.

Tier: mock (mock Manager; nothing leaves the machine).
"""
from __future__ import annotations

import pytest

from app.models import ManagerStep
from app.workforce import control
from tests import wf as h

PLAN = """Deliver:
1. Research it [research]
2. Build it [engineering] (after 1)"""


def test_mock_manager_without_window_plans(db):
    h.reset(db)
    wf = h.planned(db, PLAN)
    assert set(h.tasks_of(db, wf)) == {"Research it", "Build it"}
    step = db.query(ManagerStep).filter(ManagerStep.workflow_id == wf.id, ManagerStep.status == "ok").first()
    assert step is not None and step.provider == "mock" and step.is_mock is True


def test_manager_without_model_or_window_is_refused_at_submit(db):
    h.reset(db, manager=None)
    with pytest.raises(control.ControlError, match="manager_model_not_configured"):
        h.objective(db, PLAN)
    db.rollback()
