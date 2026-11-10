"""GW-07 routing, GW-08 budgets, AG-07 teams, OR-09 existing-project analysis."""
from __future__ import annotations

import uuid
from decimal import Decimal

import httpx2 as httpx
import pytest
from sqlalchemy import select

import app.main  # noqa: F401
from app.connectivity.gateway import routing
from app.connectivity.gateway import service as gw
from app.governance import approvals as approvals_svc
from app.governance import budgets
from app.models import (
    Agent,
    ApiCredential,
    Approval,
    AuditLog,
    Budget,
    CostRecord,
    ModelInfo,
    Organization,
    Project,
    Provider,
    Task,
    UsageRecord,
)
from app.orchestration import inspector
from app.runtime import circuit
from app.security.crypto import encrypt_secret
from tests.test_gateway import KEY, anthropic_handler


# ---------------- GW-07 ----------------
@pytest.fixture()
def org(db):
    o = Organization(name=f"Routing {uuid.uuid4().hex[:6]}", slug=f"r-{uuid.uuid4().hex[:8]}")
    db.add(o)
    db.commit()
    return o


def _provider(db, org, name, models, priority=5, kind="anthropic", is_mock=False):
    cred = ApiCredential(organization_id=org.id, name=f"{name} key", kind="api_key", encrypted_value=encrypt_secret(KEY),
                         hint="WXYZ")
    db.add(cred)
    db.flush()
    p = Provider(organization_id=org.id, name=name, kind=kind, credential_id=cred.id, enabled=True, priority=priority,
                 is_mock=is_mock, status="ok")
    db.add(p)
    db.flush()
    for m in models:
        db.add(ModelInfo(provider_id=p.id, **m))
    db.commit()
    db.refresh(p)
    return p


def _agent(db, org, **kw):
    kw.setdefault("context_policy", {"routing": "auto"})
    a = Agent(organization_id=org.id, name=f"R {uuid.uuid4().hex[:5]}", identifier=f"rt-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", system_instructions="Be precise.",
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1}, **kw)
    db.add(a)
    db.commit()
    return a


CHEAP = {"name": "small", "tier": 1, "capabilities": ["tools"], "context_window": 32_000,
         "input_price_per_mtok": Decimal("0.25"), "output_price_per_mtok": Decimal("1.25")}
MID = {"name": "medium", "tier": 2, "capabilities": ["tools", "vision"], "context_window": 200_000,
       "input_price_per_mtok": Decimal(3), "output_price_per_mtok": Decimal(15)}
BIG = {"name": "large", "tier": 3, "capabilities": ["tools", "vision", "reasoning"], "context_window": 200_000,
       "input_price_per_mtok": Decimal(15), "output_price_per_mtok": Decimal(75)}


def test_routes_by_complexity(db, org):
    _provider(db, org, "A", [CHEAP, MID, BIG])
    a = _agent(db, org)
    simple = routing.rank(db, a, routing.assess(a, {"prompt": "rename a variable"}))[0]
    assert simple[0].model.name == "small"
    hard = routing.rank(db, a, routing.assess(a, {"prompt": "review the auth flow"}, "security"))[0]
    assert hard[0].model.name == "large"
    hinted = routing.rank(db, a, routing.assess(a, {"prompt": "x", "complexity": "medium"}))[0]
    assert hinted[0].model.name == "medium"


def test_routes_by_capability_and_context_window(db, org):
    _provider(db, org, "A", [{**CHEAP, "capabilities": []}, MID])
    a = _agent(db, org, tool_permissions=["files.write"])
    ranked, rejected = routing.rank(db, a, routing.assess(a, {"prompt": "short"}))
    assert [c.model.name for c in ranked] == ["medium"]
    assert any(r["model"] == "small" and "capabilities" in r["reason"] for r in rejected)
    a2 = _agent(db, org)
    long = "word " * 60_000  # ~60k tokens > 32k window
    ranked, rejected = routing.rank(db, a2, routing.assess(a2, {"prompt": long}))
    assert "small" not in [c.model.name for c in ranked]
    assert any("context window" in r["reason"] for r in rejected)
    ranked, _ = routing.rank(db, a2, routing.assess(a2, {"prompt": "look", "images": ["x"]}))
    assert [c.model.name for c in ranked] == ["medium"]  # vision needed


def test_routes_by_cost_availability_and_user_config(db, org):
    pricey = _provider(db, org, "Pricey", [{**MID, "name": "m-expensive", "input_price_per_mtok": Decimal(9),
                                             "output_price_per_mtok": Decimal(40)}])
    cheap = _provider(db, org, "Cheap", [{**MID, "name": "m-cheap"}])
    _provider(db, org, "Mock", [{**MID, "name": "mock-model", "input_price_per_mtok": None,
                                 "output_price_per_mtok": None}], kind="mock", is_mock=True)
    a = _agent(db, org)
    need = routing.assess(a, {"prompt": "x", "complexity": "medium"})
    ranked, rejected = routing.rank(db, a, need)
    assert [c.model.name for c in ranked] == ["m-cheap", "m-expensive"]  # same tier: cheaper first, mock excluded
    assert any(r["provider"] == "Mock" and "mock" in r["reason"] for r in rejected)
    # availability: an open circuit removes the provider
    for _ in range(10):
        circuit.record_failure(db, "provider", cheap.id, org.id, "down")
    db.commit()
    ranked, rejected = routing.rank(db, a, need)
    assert [c.model.name for c in ranked] == ["m-expensive"]
    assert any(r["provider"] == "Cheap" and "circuit" in r["reason"] for r in rejected)
    # user configuration
    a2 = _agent(db, org, context_policy={"routing": "auto", "allowed_provider_ids": [pricey.id]})
    assert [c.provider.name for c in routing.rank(db, a2, need)[0]] == ["Pricey"]
    a3 = _agent(db, org, context_policy={"routing": "auto", "allow_mock": True})
    assert "mock-model" in [c.model.name for c in routing.rank(db, a3, need)[0]]


def test_provider_budget_removes_it_from_routing(db, org):
    p = _provider(db, org, "Capped", [MID])
    _provider(db, org, "Other", [{**MID, "name": "other"}])
    db.add(Budget(organization_id=org.id, scope="provider", scope_id=p.id, period="monthly", limit_tokens=100))
    db.add(UsageRecord(organization_id=org.id, provider_id=p.id, provider_kind="anthropic", model="medium",
                       input_tokens=80, output_tokens=40))
    db.commit()
    a = _agent(db, org)
    ranked, rejected = routing.rank(db, a, routing.assess(a, {"prompt": "x", "complexity": "medium"}))
    assert [c.provider.name for c in ranked] == ["Other"]
    assert any(r["provider"] == "Capped" and "budget" in r["reason"] for r in rejected)


def test_auto_routed_agent_runs_end_to_end_and_records_the_decision(db, org):
    import asyncio

    from app.runtime import service
    gw.HTTP_CLIENTS["anthropic"] = httpx.AsyncClient(transport=httpx.MockTransport(anthropic_handler()))
    try:
        _provider(db, org, "Real", [{**CHEAP, "name": "claude-test"}, BIG])
        a = _agent(db, org)
        ex = service.create_execution(db, organization_id=org.id, agent=a, input={"prompt": "Say hi"}, actor="test")
        db.commit()
        asyncio.run(service.run_execution(ex.id))
    finally:
        gw.HTTP_CLIENTS.pop("anthropic", None)
    db.expire_all()
    ex = db.get(type(ex), ex.id)
    assert ex.status == "COMPLETED", ex.error
    assert ex.status == "COMPLETED" and ex.model == "claude-test"  # short prompt → tier-1 model
    log = db.scalar(select(AuditLog).where(AuditLog.action == "gateway.routed", AuditLog.execution_id == ex.id))
    assert log.details["candidates"][0]["model"] == "claude-test" and log.details["need"]["tier"] == 1


def test_routing_preview_endpoint(admin_client, db):
    a = Agent(organization_id=1, name=f"P {uuid.uuid4().hex[:5]}", identifier=f"pv-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", context_policy={"routing": "auto",
                                                                                     "allow_mock": True})
    db.add(a)
    db.commit()
    r = admin_client.post("/api/routing/preview", json={"agent_id": a.id, "prompt": "hello", "complexity": "low"})
    assert r.status_code == 200 and r.json()["need"]["tier"] == 1 and "candidates" in r.json()


# ---------------- GW-08 ----------------
def _spend(db, org_id, usd, **scope):
    u = UsageRecord(organization_id=org_id, provider_kind="anthropic", model="m", input_tokens=10, output_tokens=10,
                    **scope)
    db.add(u)
    db.flush()
    db.add(CostRecord(organization_id=org_id, usage_record_id=u.id, amount_usd=Decimal(usd),
                      **{k: v for k, v in scope.items() if k in ("agent_id", "project_id", "task_id")}))
    db.commit()


def _mock_agent(db, admin_client):
    mock_id = next(x["id"] for x in admin_client.get("/api/providers").json() if x["kind"] == "mock")
    a = Agent(organization_id=1, name=f"B {uuid.uuid4().hex[:5]}", identifier=f"bd-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="api", provider_id=mock_id, model="mock-model",
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1})
    db.add(a)
    db.commit()
    return a


def test_budget_stops_new_work_requests_approval_and_resumes_after_increase(admin_client, db, make_user):
    capped, free = _mock_agent(db, admin_client), _mock_agent(db, admin_client)
    r = admin_client.post("/api/budgets", json={"scope": "agent", "scope_id": capped.id, "period": "monthly",
                                                "limit_usd": "0.0050"})
    assert r.status_code == 201, r.text
    bid = r.json()["id"]
    _spend(db, 1, "0.0060", agent_id=capped.id)
    st = next(b for b in admin_client.get("/api/budgets").json() if b["id"] == bid)
    assert st["exceeded"] is True and Decimal(st["spent_usd"]) == Decimal("0.0060")

    r = admin_client.post("/api/executions", json={"agent_id": capped.id, "input": {"prompt": "x"}})
    assert r.status_code == 402 and r.json()["detail"] == "budget_exceeded"
    r2 = admin_client.post("/api/executions", json={"agent_id": capped.id, "input": {"prompt": "x"}})
    assert r2.status_code == 402
    pending = db.scalars(select(Approval).where(Approval.operation == "budget.increase",
                                                Approval.status == "pending")).all()
    mine = [a for a in pending if a.payload.get("budget_id") == bid]
    assert len(mine) == 1  # one request per exhausted budget, not one per attempt
    assert mine[0].payload["increase_usd"] == "0.0025"
    assert db.scalar(select(AuditLog).where(AuditLog.action == "budget.exceeded", AuditLog.agent_id == capped.id))
    # unrelated work keeps running
    assert admin_client.post("/api/executions", json={"agent_id": free.id, "input": {"prompt": "x"}}).status_code == 201

    approver = make_user("budget-approver", "ORGANIZATION_ADMIN")
    approvals_svc.decide(db, mine[0], user_id=approver.id, decision="approve", reason="ok")
    db.commit()
    db.expire_all()
    assert db.get(Budget, bid).limit_usd == Decimal("0.0075")
    assert admin_client.post("/api/executions", json={"agent_id": capped.id, "input": {"prompt": "x"}}).status_code == 201


def test_token_daily_project_and_implicit_task_budgets(db, admin_client):
    p = Project(organization_id=1, name=f"Bp {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.flush()
    t1 = Task(organization_id=1, project_id=p.id, title="a", budget_usd=Decimal("0.01"))
    t2 = Task(organization_id=1, project_id=p.id, title="b")
    db.add_all([t1, t2])
    db.commit()
    _spend(db, 1, "0.02", project_id=p.id, task_id=t1.id)
    assert budgets.exceeded(db, 1, project_id=p.id, task_id=t1.id)        # the task's own budget
    assert not budgets.exceeded(db, 1, project_id=p.id, task_id=t2.id)    # the sibling task continues
    db.add(Budget(organization_id=1, scope="project", scope_id=p.id, period="daily", limit_tokens=15))
    db.commit()
    hit = budgets.exceeded(db, 1, project_id=p.id, task_id=t2.id)          # 20 tokens today ≥ 15
    assert hit and hit[0].limit_tokens == 15
    # mock usage never counts and nothing is estimated
    mock_u = UsageRecord(organization_id=1, provider_kind="mock", model="m", input_tokens=10**6, output_tokens=0,
                         is_mock=True, agent_id=None, project_id=None)
    db.add(mock_u)
    db.commit()
    assert budgets.spent(db, 1, "global", None, "daily")[1] < 10**6
    assert admin_client.post("/api/budgets", json={"scope": "agent", "period": "daily",
                                                   "limit_usd": "1"}).status_code == 422


def test_budget_management_needs_permission(client, make_user):
    from tests.conftest import login_as
    make_user("bud-dev", "DEVELOPER")
    login_as(client, "bud-dev", "user-password-123")
    assert client.post("/api/budgets", json={"scope": "global", "period": "daily", "limit_usd": "1"}).status_code == 403


# ---------------- AG-07 ----------------
def test_team_crud_and_membership(admin_client, db, make_user, client):
    from tests.conftest import login_as
    agents = [Agent(organization_id=1, name=f"T {uuid.uuid4().hex[:5]}", identifier=f"tm-{uuid.uuid4().hex[:8]}",
                    role="backend", state="IDLE", execution_engine="api") for _ in range(3)]
    db.add_all(agents)
    db.commit()
    member = make_user("team-member", "DEVELOPER")
    name = f"Team {uuid.uuid4().hex[:6]}"
    r = admin_client.post("/api/teams", json={"name": name, "agent_ids": [agents[0].id, agents[1].id],
                                              "user_ids": [member.id], "lead_agent_id": agents[0].id})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["agent_ids"] == sorted([agents[0].id, agents[1].id]) and t["user_ids"] == [member.id]
    assert admin_client.post("/api/teams", json={"name": name}).status_code == 409
    bad = admin_client.post("/api/teams", json={"name": "x" + name, "agent_ids": [agents[2].id],
                                                "lead_agent_id": agents[0].id})
    assert bad.status_code == 422 and bad.json()["detail"] == "lead_must_be_a_member"
    # removing the lead from the members clears the lead
    u = admin_client.patch(f"/api/teams/{t['id']}", json={"agent_ids": [agents[1].id, agents[2].id]}).json()
    assert u["agent_ids"] == sorted([agents[1].id, agents[2].id]) and u["lead_agent_id"] is None
    u = admin_client.patch(f"/api/teams/{t['id']}", json={"lead_agent_id": agents[2].id, "description": "d"}).json()
    assert u["lead_agent_id"] == agents[2].id and u["description"] == "d"
    # cross-tenant members are refused
    other = Organization(name=f"O {uuid.uuid4().hex[:5]}", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    foreign = Agent(organization_id=other.id, name="F", identifier=f"f-{uuid.uuid4().hex[:8]}", role="backend",
                    state="IDLE", execution_engine="api")
    db.add(foreign)
    db.commit()
    assert admin_client.patch(f"/api/teams/{t['id']}", json={"agent_ids": [foreign.id]}).status_code == 404
    # projects can belong to a team; deleting the team keeps the project
    p = admin_client.post("/api/projects", json={"name": f"TP {uuid.uuid4().hex[:6]}"}).json()
    assert admin_client.patch(f"/api/projects/{p['id']}", json={"team_id": t["id"]}).json()["team_id"] == t["id"]
    assert admin_client.get(f"/api/teams/{t['id']}").json()["project_ids"] == [p["id"]]
    login_as(client, "team-member", "user-password-123")
    assert client.get("/api/teams").status_code == 200
    assert client.delete(f"/api/teams/{t['id']}").status_code == 403
    client.headers.pop("x-csrf-token", None)
    login_as(client, "admin", "test-admin-password-1")
    assert admin_client.delete(f"/api/teams/{t['id']}").status_code == 204
    db.expire_all()
    assert db.get(Project, p["id"]).team_id is None
    assert {"teams.created", "teams.updated", "teams.deleted"} <= set(
        db.scalars(select(AuditLog.action).where(AuditLog.category == "teams")))


# ---------------- OR-09 ----------------
def _sample_repo(root):
    (root / "backend" / "app").mkdir(parents=True)
    (root / "backend" / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies=["fastapi>=0.110","sqlalchemy","pytest==8.0"]\n')
    (root / "backend" / "uv.lock").write_text("")
    (root / "backend" / "app" / "main.py").write_text("# TODO: split\nfrom fastapi import FastAPI\n" + "x = 1\n" * 900)
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text('{"dependencies":{"next":"15.0.0","react":"*"},'
                                                    '"devDependencies":{"@playwright/test":"1.0"}}')
    (root / "frontend" / "page.tsx").write_text("export default function P() { return null } // FIXME\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text("def test_x():\n    assert True\n")
    (root / "Dockerfile").write_text("FROM python\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.js").write_text("x")  # skipped
    (root / "link").symlink_to("/etc")                       # not followed


def test_repository_analysis_produces_a_map(tmp_path):
    _sample_repo(tmp_path)
    a = inspector.analyze(tmp_path)
    langs = {x["name"] for x in a["technologies"]["languages"]}
    assert {"Python", "TypeScript"} <= langs and "JavaScript" not in langs
    assert {"FastAPI", "SQLAlchemy", "pytest", "Next.js", "React", "Playwright"} <= set(a["technologies"]["frameworks"])
    assert "Dockerfile" in a["technologies"]["infrastructure"]
    roles = {x["dir"]: x["role"] for x in a["architecture"]}
    assert roles["backend"] == "backend" and roles["frontend"] == "frontend" and roles["tests"] == "tests"
    assert a["dependencies"]["python"]["fastapi"] == ">=0.110" and a["dependencies"]["node"]["next"] == "15.0.0"
    assert a["tests"]["files"] == 1 and "pytest" in a["tests"]["frameworks"]
    kinds = {d["kind"] for d in a["debt"]}
    assert {"markers", "large_files", "unpinned", "no_readme"} <= kinds and "no_lockfile" not in kinds
    assert a["large_files"][0]["file"].endswith("main.py")
    assert "EXISTING REPOSITORY MAP" in inspector.summary(a) and "FastAPI" in inspector.summary(a)


def test_existing_project_import_and_analyze_api(admin_client, db, tmp_path, monkeypatch):
    from app.devplane import git as g
    from app.devplane import service as svc
    from app.models import Repository
    monkeypatch.setattr(svc, "REMOTE_BASE", str(tmp_path / "remotes"))
    src = tmp_path / "src"
    src.mkdir()
    _sample_repo(src)
    (src / "link").unlink()
    g.git(src, "init", "-q", "-b", "main")
    g.git(src, "add", "-A")
    g.git(src, "commit", "-q", "-m", "init", author=g.PLATFORM)
    bare = tmp_path / "remotes" / "acme" / "shop.git"
    bare.parent.mkdir(parents=True)
    g.git(tmp_path, "clone", "-q", "--bare", str(src), str(bare))
    repo = Repository(organization_id=1, full_name="acme/shop", default_branch="main")
    db.add(repo)
    db.commit()
    p = admin_client.post("/api/projects", json={"name": f"Shop {uuid.uuid4().hex[:6]}", "mode": "existing"}).json()
    assert admin_client.post(f"/api/projects/{p['id']}/analyze").status_code == 409  # nothing imported yet
    assert admin_client.post(f"/api/projects/{p['id']}/import").json()["detail"] == "no_linked_repository"
    admin_client.put(f"/api/projects/{p['id']}/repository", json={"repository_id": repo.id})
    r = admin_client.post(f"/api/projects/{p['id']}/import")
    assert r.status_code == 200, r.text
    assert admin_client.post(f"/api/projects/{p['id']}/import").json()["detail"] == "already_imported"
    a = admin_client.post(f"/api/projects/{p['id']}/analyze").json()
    assert "FastAPI" in a["technologies"]["frameworks"] and a["tests"]["files"] == 1
    assert admin_client.get(f"/api/projects/{p['id']}/analysis").json()["files"] == a["files"]
    assert next(x for x in admin_client.get("/api/projects").json() if x["id"] == p["id"])["analyzed_at"]


def test_planner_prompt_uses_the_repository_map_for_existing_projects(db, monkeypatch):
    """The planner's user message for an existing project carries the map, fenced as data."""
    import asyncio

    from app.orchestration import planner
    captured = {}

    class FakeAdapter:
        async def stream(self, req):
            captured["user"] = req.messages[0]["content"]
            if False:
                yield None

    p = Project(organization_id=1, name=f"Ex {uuid.uuid4().hex[:6]}", mode="maintenance",
                analysis={"technologies": {"languages": [{"name": "Go", "files": 3, "lines": 10}],
                                           "frameworks": ["Gin"], "infrastructure": []},
                          "architecture": [], "tests": {"files": 0, "frameworks": []}, "debt": []})
    db.add(p)
    db.flush()
    from app.models import Workflow
    wf = Workflow(organization_id=1, project_id=p.id, title="t", objective="fix the login bug", status="PLANNING")
    db.add(wf)
    db.commit()
    agent = Agent(organization_id=1, name="Planner", identifier=f"pl-{uuid.uuid4().hex[:8]}", role="manager",
                  state="IDLE", execution_engine="api", provider_id=1, model="m", tool_permissions=["tasks.plan"])
    monkeypatch.setattr(planner, "planner_agent", lambda db_, wf_: agent)
    mock = db.scalar(select(Provider).where(Provider.organization_id == 1, Provider.kind == "mock"))
    monkeypatch.setattr(planner, "_routes", lambda db_, a_: [(db_.get(Provider, mock.id), "m")])
    monkeypatch.setattr(planner, "build_adapter", lambda prov: FakeAdapter())
    asyncio.run(planner.draft({"workflow_id": wf.id, "attempt": 0}))
    assert "PROJECT MODE: maintenance" in captured["user"]
    assert "<<<REPOSITORY MAP>>>" in captured["user"] and "Gin" in captured["user"]


# ---------------- UX-04 (API) ----------------
def test_analytics_groups_real_usage_and_never_invents_cost(admin_client, db):
    a = _mock_agent(db, admin_client)
    p = Project(organization_id=1, name=f"An {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    _spend(db, 1, "0.5", agent_id=a.id, project_id=p.id)           # priced
    db.add(UsageRecord(organization_id=1, provider_kind="x", model="unpriced-model", input_tokens=7, output_tokens=3,
                       agent_id=a.id, project_id=p.id))             # no price → no cost row
    db.add(UsageRecord(organization_id=1, provider_kind="mock", model="mock-model", input_tokens=999,
                       output_tokens=1, agent_id=a.id, is_mock=True))
    db.commit()
    by_agent = admin_client.get("/api/analytics/usage", params={"group": "agent"}).json()
    row = next(i for i in by_agent["items"] if i["key"] == str(a.id))
    assert row["label"] == a.name and row["requests"] == 2 and row["unpriced_requests"] == 1
    assert Decimal(row["cost_usd"]) == Decimal("0.5") and row["input_tokens"] == 17
    assert not any(i["is_mock"] for i in by_agent["items"])
    with_mock = admin_client.get("/api/analytics/usage", params={"group": "agent", "include_mock": True}).json()
    assert any(i["is_mock"] and i["key"] == str(a.id) for i in with_mock["items"])
    by_model = admin_client.get("/api/analytics/usage", params={"group": "model"}).json()
    unpriced = next(i for i in by_model["items"] if i["key"] == "unpriced-model")
    assert unpriced["cost_usd"] is None
    by_project = admin_client.get("/api/analytics/usage", params={"group": "project"}).json()
    assert any(i["label"] == p.name for i in by_project["items"])
    assert admin_client.get("/api/analytics/usage", params={"group": "day"}).status_code == 200


def test_runtime_overview(admin_client):
    r = admin_client.get("/api/runtime/overview").json()
    kinds = {e["kind"]: e for e in r["engines"]}
    assert {"api", "mock", "a2a", "browser"} <= set(kinds) and kinds["mock"]["is_mock"] is True
    assert isinstance(r["executions"], dict) and isinstance(r["circuits"], list)
