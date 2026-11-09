from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.models import Agent, CostRecord, Organization, Task, UsageRecord


def test_dashboard_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/dashboard/summary").status_code == 401


def test_dashboard_counts_come_from_db_and_exclude_mock_usage_and_other_orgs(admin_client, db, make_user):
    base = admin_client.get("/api/dashboard/summary").json()
    org = db.scalar(select(Organization).where(Organization.slug == "default"))
    make_user("other-org-user", "VIEWER", org_slug="other-org")
    other = db.scalar(select(Organization).where(Organization.slug == "other-org"))
    db.add_all([
        Agent(organization_id=org.id, name="A", identifier="dash-a", role="backend", state="RUNNING"),
        Agent(organization_id=org.id, name="B", identifier="dash-b", role="backend", state="IDLE"),
        Agent(organization_id=org.id, name="C", identifier="dash-c", role="backend", is_enabled=False),
        Agent(organization_id=org.id, name="D", identifier="dash-d", role="backend", archived_at=datetime.now(UTC)),
        Agent(organization_id=other.id, name="X", identifier="dash-x", role="backend", state="RUNNING"),
        Task(organization_id=org.id, title="t1", status="COMPLETED"),
        Task(organization_id=org.id, title="t2", status="FAILED"),
        Task(organization_id=org.id, title="t3", status="COMPLETED"),
    ])
    real = UsageRecord(organization_id=org.id, provider_kind="anthropic", model="real", input_tokens=100,
                       output_tokens=50, ts=datetime.now(UTC))
    db.add_all([real, UsageRecord(organization_id=org.id, provider_kind="mock", model="mock-model",
                                  input_tokens=999, output_tokens=999, is_mock=True)])
    db.flush()
    db.add(CostRecord(organization_id=org.id, usage_record_id=real.id, amount_usd=Decimal("0.5")))
    db.commit()
    s = admin_client.get("/api/dashboard/summary").json()
    assert s["agents"]["total"] == base["agents"]["total"] + 3  # archived + other org excluded
    assert s["agents"]["active"] == base["agents"]["active"] + 1
    assert s["agents"]["available"] == base["agents"]["available"] + 1
    assert s["agents"]["disabled"] == base["agents"]["disabled"] + 1
    assert s["tasks"]["COMPLETED"] == base["tasks"]["COMPLETED"] + 2
    assert s["tokens_total"] == base["tokens_total"] + 150  # mock usage excluded
    assert abs(s["cost_usd_total"] - base["cost_usd_total"] - 0.5) < 1e-9
    assert len(s["usage_series"]) == 14 and s["usage_series"][-1]["tokens"] >= 150
