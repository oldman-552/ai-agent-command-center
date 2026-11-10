"""SEC-06 — audit log search: filters, severity threshold, keyset paging, tenant isolation, redaction, CSV export."""
from __future__ import annotations

import csv
import io
import uuid

from app.models import AuditLog, Organization
from app.security.audit import log_activity


def _seed(db, tag: str):
    other = Organization(name=f"Other {tag}", slug=f"o-{tag}")
    db.add(other)
    db.flush()
    for i in range(7):
        log_activity(db, f"test.{tag}.step", category=f"cat-{tag}", level="info", organization_id=1,
                     actor="agent:5", message=f"step {i}")
    log_activity(db, f"test.{tag}.fail", category=f"cat-{tag}", level="error", organization_id=1, actor="user:1",
                 message="token sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX leaked?", details={"api_key": "sk-ant-api03-ZZZZ"})
    log_activity(db, f"test.{tag}.formula", category=f"cat-{tag}", organization_id=1, actor="user:1",
                 message="=HYPERLINK(\"http://evil\")")
    log_activity(db, f"test.{tag}.foreign", category=f"cat-{tag}", organization_id=other.id, actor="user:99",
                 message="other tenant")
    db.commit()


def test_search_filters_paging_isolation_and_redaction(admin_client, db):
    tag = uuid.uuid4().hex[:6]
    _seed(db, tag)
    base = f"/api/audit?category=cat-{tag}"
    all_ = admin_client.get(base).json()
    assert all_["total"] == 9 and f"cat-{tag}" in all_["categories"]
    assert not any("foreign" in e["action"] for e in all_["items"])  # other tenant never visible
    errors = admin_client.get(f"{base}&level=warning").json()
    assert [e["action"] for e in errors["items"]] == [f"test.{tag}.fail"]  # warning and above
    leaked = errors["items"][0]
    assert "ABCDEFGHIJ" not in leaked["message"] and "ZZZZ" not in str(leaked["details"])
    assert admin_client.get(f"{base}&actor=agent:5").json()["total"] == 7
    assert admin_client.get(f"/api/audit?q=step%205&category=cat-{tag}").json()["total"] == 1
    assert admin_client.get(f"/api/audit?q=%25&category=cat-{tag}").json()["total"] == 0  # % is literal
    p1 = admin_client.get(f"{base}&limit=4").json()
    p2 = admin_client.get(f"{base}&limit=4&before_id={p1['next_before_id']}").json()
    p3 = admin_client.get(f"{base}&limit=4&before_id={p2['next_before_id']}").json()
    ids = [e["id"] for p in (p1, p2, p3) for e in p["items"]]
    assert len(ids) == 9 == len(set(ids)) and ids == sorted(ids, reverse=True) and p3["next_before_id"] is None
    assert admin_client.get(f"{base}&level=loud").status_code == 422


def test_csv_export_is_filtered_redacted_and_formula_safe(admin_client, db):
    tag = uuid.uuid4().hex[:6]
    _seed(db, tag)
    r = admin_client.get(f"/api/audit/export.csv?category=cat-{tag}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 9
    assert all("ABCDEFGHIJ" not in row["message"] for row in rows)
    formula = next(row for row in rows if row["action"].endswith("formula"))
    assert formula["message"].startswith("'=")


def test_every_role_reads_but_nobody_writes(client, make_user, db):
    from tests.conftest import login_as
    make_user("viewer-audit", "VIEWER")
    v = login_as(client, "viewer-audit", "user-password-123")
    assert v.get("/api/audit").status_code == 200
    assert v.post("/api/audit", json={}).status_code == 405
    assert v.delete("/api/audit").status_code == 405
    assert db.query(AuditLog).count() > 0
