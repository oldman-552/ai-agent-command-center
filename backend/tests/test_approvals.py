"""SEC-03 — Approval Center: backend-enforced, human-only, tamper-evident, executed exactly once."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import app.main  # noqa: F401
from app.governance import approvals as svc
from app.models import Agent, AgentConfigVersion, Approval, AuditLog, Organization
from tests.conftest import login_as


def _agent(db, org=1, profile="developer", tools=("repo.read",)) -> Agent:
    a = Agent(organization_id=org, name=f"Ap {uuid.uuid4().hex[:5]}", identifier=f"ap-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", security_profile=profile,
              tool_permissions=list(tools))
    db.add(a)
    db.commit()
    return a


def _escalation(db, agent, add=("files.write",), **kw) -> Approval:
    a = svc.request_approval(db, organization_id=agent.organization_id, operation="permission.escalation",
                             title=f"Grant {add} to {agent.name}", requested_by=f"agent:{agent.id}",
                             payload={"agent_id": agent.id, "add_tools": list(add)}, agent_id=agent.id, **kw)
    db.commit()
    return a


def test_request_freezes_payload_and_never_lowers_risk(db):
    ag = _agent(db)
    a = _escalation(db, ag, risk_level="low")
    assert a.status == "pending" and a.risk_level == "high"  # catalogue minimum wins
    assert a.evidence[0] == {"kind": "payload", "sha256": svc.payload_hash(a.payload), "at": a.evidence[0]["at"]}
    assert db.scalar(select(AuditLog).where(AuditLog.action == "approvals.requested",
                                            AuditLog.details["approval_id"].as_integer() == a.id))
    k = f"k-{uuid.uuid4()}"
    assert _escalation(db, ag, idempotency_key=k).id == _escalation(db, ag, idempotency_key=k).id
    with pytest.raises(ValueError):
        svc.request_approval(db, organization_id=1, operation="rm -rf", title="x", requested_by="agent:1", payload={})


def test_approval_executes_exact_payload_once(admin_client, db):
    ag = _agent(db)
    a = _escalation(db, ag)
    r = admin_client.post(f"/api/approvals/{a.id}/decide", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "executed" and r.json()["executed_at"]
    db.refresh(ag)
    assert ag.tool_permissions == ["files.write", "repo.read"] and ag.config_version == 2
    assert db.scalar(select(AgentConfigVersion).where(AgentConfigVersion.agent_id == ag.id,
                                                      AgentConfigVersion.version == 2))
    again = admin_client.post(f"/api/approvals/{a.id}/decide", json={"decision": "approve"})
    assert again.status_code == 409 and again.json()["detail"] == "approval_not_pending:executed"


def test_tampered_payload_is_refused_but_decision_is_kept(admin_client, db):
    ag = _agent(db)
    a = _escalation(db, ag)
    a.payload = {"agent_id": ag.id, "add_tools": ["files.write", "git.commit"]}  # changed after the request
    db.commit()
    body = admin_client.post(f"/api/approvals/{a.id}/decide", json={"decision": "approve"}).json()
    assert body["status"] == "approved" and body["executed_at"] is None
    assert body["evidence"][-1]["kind"] == "execution_failed"
    assert body["evidence"][-1]["error"] == "payload_changed_after_request"
    db.refresh(ag)
    assert ag.tool_permissions == ["repo.read"]


def test_approval_cannot_lift_the_security_profile_ceiling(admin_client, db):
    ag = _agent(db, profile="read_only")
    a = _escalation(db, ag, add=("files.write",))
    body = admin_client.post(f"/api/approvals/{a.id}/decide", json={"decision": "approve"}).json()
    assert body["status"] == "approved" and body["evidence"][-1]["error"].startswith("outside_security_profile")
    db.refresh(ag)
    assert ag.tool_permissions == ["repo.read"]


def test_only_humans_with_decide_permission_and_never_on_own_request(admin_client, client, db, make_user):
    ag = _agent(db)
    a = _escalation(db, ag)
    me = admin_client.get("/api/auth/me").json()["id"]
    own = svc.request_approval(db, organization_id=1, operation="credential.change", title="rotate",
                               requested_by=f"user:{me}", payload={"provider_id": 1})
    db.commit()
    r = admin_client.post(f"/api/approvals/{own.id}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["detail"] == "self_approval_forbidden"
    r = admin_client.post(f"/api/approvals/{a.id}/decide", json={"decision": "reject"})
    assert r.status_code == 422 and r.json()["detail"] == "reason_required"
    r = admin_client.post(f"/api/approvals/{a.id}/decide", json={"decision": "changes_requested", "reason": "narrower"})
    assert r.json()["status"] == "changes_requested" and r.json()["approver_id"] == me
    pending = _escalation(db, ag)
    make_user("dev-ap", "DEVELOPER")  # the same test client now logs in as a developer
    client.headers.pop("x-csrf-token", None)
    dev = login_as(client, "dev-ap", "user-password-123")
    assert dev.post(f"/api/approvals/{pending.id}/decide", json={"decision": "approve"}).status_code == 403
    db.refresh(pending)
    assert pending.status == "pending"


def test_listing_pending_first_expiry_and_tenant_isolation(admin_client, db):
    ag = _agent(db)
    old = _escalation(db, ag)
    old.created_at = datetime.now(UTC) - svc.PENDING_TTL - timedelta(minutes=1)
    db.commit()
    fresh = _escalation(db, ag)
    other_org = Organization(name=f"Other {uuid.uuid4().hex[:6]}", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(other_org)
    db.commit()
    foreign = _escalation(db, _agent(db, org=other_org.id))
    body = admin_client.get("/api/approvals").json()
    ids = [x["id"] for x in body["items"]]
    assert foreign.id not in ids and fresh.id in ids
    assert body["items"][0]["status"] == "pending"
    db.refresh(old)
    assert old.status == "expired"
    assert admin_client.get(f"/api/approvals/{foreign.id}").status_code == 404
    assert admin_client.get("/api/approvals?status=pending").json()["items"][0]["operation_label"] == "Permission escalation"
    with pytest.raises(HTTPException):
        svc.decide(db, old, user_id=1, decision="approve", reason="")
