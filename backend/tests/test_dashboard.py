from datetime import UTC, datetime
from decimal import Decimal

from app.models import Agent, AgentRole, Task, UsageStatistic


def test_dashboard_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/dashboard/summary").status_code == 401


def test_dashboard_counts_come_from_db_and_exclude_mock_usage(admin_client, db):
    base = admin_client.get("/api/dashboard/summary").json()
    role = db.query(AgentRole).filter_by(key="backend").one()
    db.add_all([
        Agent(name="A", identifier="dash-a", role_id=role.id, runtime_status="running"),
        Agent(name="B", identifier="dash-b", role_id=role.id, runtime_status="idle"),
        Agent(name="C", identifier="dash-c", role_id=role.id, is_enabled=False),
        Agent(name="D", identifier="dash-d", role_id=role.id, is_archived=True),
        Task(title="t1", status="completed"), Task(title="t2", status="failed"),
        Task(title="t3", status="completed"),
        UsageStatistic(model="real", input_tokens=100, output_tokens=50, cost_usd=Decimal("0.5"),
                       ts=datetime.now(UTC)),
        UsageStatistic(model="mock-model", input_tokens=999, output_tokens=999, is_mock=True),
    ])
    db.commit()
    s = admin_client.get("/api/dashboard/summary").json()
    assert s["agents"]["total"] == base["agents"]["total"] + 3  # archived excluded
    assert s["agents"]["active"] == base["agents"]["active"] + 1
    assert s["agents"]["available"] == base["agents"]["available"] + 1
    assert s["agents"]["disabled"] == base["agents"]["disabled"] + 1
    assert s["tasks"]["completed"] == base["tasks"]["completed"] + 2
    assert s["tokens_total"] == base["tokens_total"] + 150  # mock usage excluded
    assert len(s["usage_series"]) == 14
    assert s["usage_series"][-1]["tokens"] >= 150
    assert s["task_success_rate"] is not None
