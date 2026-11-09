"""SEC-01 (server-side RBAC), SEC-02 (no escalation), SEC-07 (CSRF), FND-15 (tenant isolation)."""
from fastapi.testclient import TestClient

from app.security.rbac import PERMISSIONS, ROLES, role_grants
from tests.conftest import login_as

PW = "user-password-123"


def fresh(app_client_factory=None):
    from app.main import app
    return TestClient(app)


def test_six_roles_seeded_with_expected_permissions(admin_client):
    roles = {r["key"]: set(r["permissions"]) for r in admin_client.get("/api/roles").json()}
    assert set(roles) == set(ROLES)
    assert roles["SUPER_ADMIN"] == set(PERMISSIONS)
    assert "security.manage" not in roles["ORGANIZATION_ADMIN"]
    assert roles["VIEWER"] == role_grants("VIEWER") and all(p.endswith(".read") for p in roles["VIEWER"])
    assert "approvals.decide" not in roles["DEVELOPER"]


def test_permissions_enforced_server_side(admin_client, make_user):
    make_user("viewer1", "VIEWER")
    with fresh() as c:
        login_as(c, "viewer1", PW)
        assert c.get("/api/dashboard/summary").status_code == 200       # has dashboard.read
        assert c.get("/api/users").status_code == 403                   # lacks users.manage
        assert c.post("/api/users", json={"username": "x1", "password": "p" * 12, "role": "VIEWER"}).status_code == 403


def test_no_privilege_escalation(admin_client, make_user):
    make_user("orgadmin1", "ORGANIZATION_ADMIN")
    with fresh() as c:
        login_as(c, "orgadmin1", PW)
        r = c.post("/api/users", json={"username": "sneaky", "password": "p" * 12, "role": "SUPER_ADMIN"})
        assert r.status_code == 403 and r.json()["detail"] == "cannot_grant_higher_privileges"
        ok = c.post("/api/users", json={"username": "dev1", "password": "p" * 12, "role": "DEVELOPER"})
        assert ok.status_code == 201
        # cannot modify the super admin, nor self
        admin_id = next(u["id"] for u in c.get("/api/users").json() if u["role"] == "SUPER_ADMIN")
        assert c.patch(f"/api/users/{admin_id}", json={"is_active": False}).status_code == 403
        me = c.get("/api/auth/me").json()["id"]
        assert c.patch(f"/api/users/{me}", json={"role": "VIEWER"}).status_code == 400


def test_role_change_invalidates_sessions(admin_client, make_user):
    u = make_user("dev2", "DEVELOPER")
    with fresh() as c:
        login_as(c, "dev2", PW)
        assert admin_client.patch(f"/api/users/{u.id}", json={"role": "VIEWER"}).status_code == 200
        assert c.get("/api/auth/me").status_code == 401


def test_csrf_required_for_cookie_auth_writes(admin_client):
    token = admin_client.headers.pop("x-csrf-token")
    r = admin_client.put("/api/auth/me/language", json={"language": "ar"})
    assert r.status_code == 403 and r.json()["detail"] == "csrf_failed"
    r = admin_client.put("/api/auth/me/language", json={"language": "ar"}, headers={"x-csrf-token": "forged"})
    assert r.status_code == 403
    admin_client.headers["x-csrf-token"] = token
    assert admin_client.put("/api/auth/me/language", json={"language": "ar"}).status_code == 200
    assert admin_client.get("/api/auth/me").status_code == 200  # safe methods need no token


def test_tenant_isolation(admin_client, make_user):
    outsider = make_user("outsider", "VIEWER", org_slug="org-b")
    assert all(u["username"] != "outsider" for u in admin_client.get("/api/users").json())
    assert admin_client.patch(f"/api/users/{outsider.id}", json={"is_active": False}).status_code == 404


def test_security_headers_and_no_cors(client):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in r.headers
