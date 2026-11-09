"""M3 — AG-01..AG-05, plus SEC-02 tool ceilings."""
from sqlalchemy import select

from app.models import AgentConfigVersion, AgentStateTransition, Project, Provider

FULL = {
    "name": "Backend Builder", "description": "Builds APIs", "role": "backend", "avatar": "server",
    "system_instructions": "Write tested code.", "model": "mock-model", "fallback_model": "mock-model",
    "execution_engine": "api", "tool_permissions": ["repo.read", "files.write", "shell.test"],
    "skills": ["fastapi"], "mcp_server_ids": [], "memory_profile": {"scopes": ["project"]},
    "context_policy": {"task_types": ["backend"]}, "security_profile": "developer",
    "token_budget": 100000, "cost_budget_usd": "5.00", "timeout_seconds": 600,
    "retry_policy": {"max_attempts": 2, "backoff_seconds": 1, "backoff_multiplier": 2},
    "concurrency_limit": 2, "priority": 7,
}


def _mock_provider_id(db):
    return db.scalar(select(Provider.id).where(Provider.kind == "mock"))


def test_create_with_all_fields_and_read_back(admin_client, db):
    pid = _mock_provider_id(db)
    proj = Project(organization_id=1, name="agents-proj")
    db.add(proj)
    db.commit()
    body = FULL | {"provider_id": pid, "fallback_provider_id": pid, "project_ids": [proj.id]}
    r = admin_client.post("/api/agents", json=body)
    assert r.status_code == 201, r.text
    a = r.json()
    for k in ("name", "role", "avatar", "model", "execution_engine", "security_profile", "timeout_seconds",
              "concurrency_limit", "priority", "token_budget", "skills", "memory_profile", "context_policy"):
        assert a[k] == body[k], k
    assert a["identifier"] == "backend-builder" and a["state"] == "CREATED" and a["config_version"] == 1
    assert a["project_ids"] == [proj.id] and a["retry_policy"]["max_attempts"] == 2
    assert admin_client.get(f"/api/agents/{a['id']}").json()["provider_id"] == pid
    assert any(x["id"] == a["id"] for x in admin_client.get("/api/agents?q=builder").json())


def test_identifier_conflict_and_unknown_fields_rejected(admin_client):
    admin_client.post("/api/agents", json={"name": "Dup", "role": "qa", "identifier": "dup-agent"})
    assert admin_client.post("/api/agents", json={"name": "Dup2", "role": "qa", "identifier": "dup-agent"}).status_code == 409
    assert admin_client.post("/api/agents", json={"name": "X", "role": "qa", "hack": 1}).status_code == 422


def test_tool_ceiling_by_security_profile(admin_client):
    r = admin_client.post("/api/agents", json={"name": "RO", "role": "research", "security_profile": "read_only",
                                               "tool_permissions": ["repo.read", "files.write"]})
    assert r.status_code == 422 and "files.write" in r.json()["detail"]
    r = admin_client.post("/api/agents", json={"name": "Bogus", "role": "x", "tool_permissions": ["git.merge"]})
    assert r.status_code == 422  # no merge tool exists at all — merges go through approvals
    ok = admin_client.post("/api/agents", json={"name": "RO2", "role": "research", "security_profile": "read_only",
                                                "tool_permissions": ["repo.read"]}).json()
    # escalating tools via edit is also blocked
    assert admin_client.patch(f"/api/agents/{ok['id']}", json={"tool_permissions": ["shell.build"]}).status_code == 422


def test_edit_creates_config_version(admin_client, db):
    a = admin_client.post("/api/agents", json={"name": "Versioned", "role": "qa"}).json()
    r = admin_client.patch(f"/api/agents/{a['id']}", json={"priority": 9, "system_instructions": "v2"})
    assert r.json()["config_version"] == 2
    vs = admin_client.get(f"/api/agents/{a['id']}/versions").json()
    assert [v["version"] for v in vs] == [2, 1] and vs[0]["snapshot"]["priority"] == 9
    assert db.scalar(select(AgentConfigVersion).where(AgentConfigVersion.agent_id == a["id"],
                                                      AgentConfigVersion.version == 1)).snapshot["priority"] == 5


def test_clone_enable_disable_start_stop_archive_delete(admin_client, db):
    a = admin_client.post("/api/agents", json={"name": "Life", "role": "qa"}).json()
    c = admin_client.post(f"/api/agents/{a['id']}/clone").json()
    assert c["identifier"] == "life-copy" and c["name"] == "Life (copy)" and c["id"] != a["id"]
    assert admin_client.post(f"/api/agents/{a['id']}/start").json()["state"] == "IDLE"
    assert admin_client.post(f"/api/agents/{a['id']}/disable").json()["state"] == "DISABLED"
    assert admin_client.post(f"/api/agents/{a['id']}/start").status_code == 409  # disabled
    r = admin_client.post(f"/api/agents/{a['id']}/enable").json()
    assert r["state"] == "IDLE" and r["is_enabled"]
    assert admin_client.delete(f"/api/agents/{a['id']}").status_code == 409  # must archive first
    assert admin_client.post(f"/api/agents/{a['id']}/archive").json()["archived_at"]
    assert all(x["id"] != a["id"] for x in admin_client.get("/api/agents").json())
    assert any(x["id"] == a["id"] for x in admin_client.get("/api/agents?archived=true").json())
    assert admin_client.delete(f"/api/agents/{a['id']}").status_code == 204
    assert admin_client.get(f"/api/agents/{a['id']}").status_code == 404


def test_lifecycle_transitions_recorded_and_illegal_rejected(admin_client, db):
    a = admin_client.post("/api/agents", json={"name": "Trans", "role": "qa"}).json()
    admin_client.post(f"/api/agents/{a['id']}/start")
    seq = [(t["from"], t["to"]) for t in reversed(admin_client.get(f"/api/agents/{a['id']}/transitions").json())]
    assert seq == [(None, "CREATED"), ("CREATED", "INITIALIZING"), ("INITIALIZING", "IDLE")]
    from app.control.lifecycle import transition
    from app.models import Agent, AgentState
    agent = db.get(Agent, a["id"])
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        transition(db, agent, AgentState.COMPLETED, actor="test")  # IDLE -> COMPLETED is illegal
    assert db.scalar(select(AgentStateTransition).where(AgentStateTransition.agent_id == a["id"],
                                                        AgentStateTransition.to_state == "COMPLETED")) is None


def test_templates_18_customizable_and_instantiate(admin_client):
    ts = admin_client.get("/api/agent-templates").json()
    assert len([t for t in ts if t["is_system"]]) == 18
    cto = next(t for t in ts if t["key"] == "cto")
    assert admin_client.patch(f"/api/agent-templates/{cto['id']}", json={"name_ar": "المدير التقني الأول"}).status_code == 200
    a = admin_client.post(f"/api/agent-templates/{cto['id']}/instantiate?name=My%20CTO").json()
    assert a["template_id"] == cto["id"] and a["security_profile"] == "manager" and "tasks.plan" in a["tool_permissions"]
    assert a["identifier"].startswith("cto")
    ar = admin_client.post(f"/api/agent-templates/{cto['id']}/instantiate?lang=ar").json()
    assert ar["name"] == "المدير التقني الأول" and ar["description"] == cto["description_ar"]
    assert ar["identifier"].startswith("cto-")  # Arabic name never yields a meaningless slug
    custom = admin_client.post("/api/agent-templates", json={"key": "my_tpl", "name_ar": "قالبي", "name_en": "Mine",
                                                             "role": "qa", "tool_permissions": ["repo.read"]})
    assert custom.status_code == 201 and custom.json()["is_system"] is False


def test_viewer_cannot_write_agents(admin_client, make_user):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import login_as
    make_user("agentviewer", "VIEWER")
    with TestClient(app) as c:
        login_as(c, "agentviewer", "user-password-123")
        assert c.get("/api/agents").status_code == 200
        assert c.post("/api/agents", json={"name": "n", "role": "r"}).status_code == 403
