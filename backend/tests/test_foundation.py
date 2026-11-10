from sqlalchemy import func, select

from app.models import AgentTemplate, AuditLog, Provider, Role, User
from app.security.auth import login_throttle
from app.security.crypto import (
    decrypt_secret,
    encrypt_secret,
    hash_password,
    redact,
    redact_obj,
    secret_hint,
    verify_password,
)
from tests.conftest import ADMIN_PW


# ---------- health / bootstrap ----------
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "ok" and body["status"] in ("ok", "degraded")  # degraded = no worker heartbeat
    assert {"database", "rabbitmq", "redis", "workers", "browser_bridge"} <= set(body["services"])


def test_bootstrap_creates_single_super_admin_roles_and_18_templates(client, db):
    sa = db.scalar(select(Role).where(Role.key == "SUPER_ADMIN"))
    admins = db.scalars(select(User).where(User.role_id == sa.id)).all()
    assert len(admins) == 1
    assert admins[0].password_hash != ADMIN_PW and admins[0].password_hash.startswith("$argon2")
    assert set(db.scalars(select(Role.key))) >= {"SUPER_ADMIN", "ORGANIZATION_ADMIN", "PROJECT_MANAGER",
                                                 "DEVELOPER", "REVIEWER", "VIEWER"}
    assert db.scalar(select(func.count()).select_from(AgentTemplate).where(AgentTemplate.is_system)) == 18


def test_bootstrap_is_idempotent(client, db):
    from app.bootstrap import run_bootstrap

    before = db.scalar(select(func.count()).select_from(AgentTemplate))
    run_bootstrap(db)
    run_bootstrap(db)
    assert db.scalar(select(func.count()).select_from(AgentTemplate)) == before
    sa = db.scalar(select(Role).where(Role.key == "SUPER_ADMIN"))
    assert db.scalar(select(func.count()).select_from(User).where(User.role_id == sa.id)) == 1


def test_mock_provider_is_clearly_labeled(client, db):
    mock = db.scalar(select(Provider).where(Provider.kind == "mock"))
    assert mock.is_mock is True
    assert "TEST ONLY" in mock.name and "not real AI" in mock.name
    assert mock.models[0].name == "mock-model"


# ---------- authentication ----------
def test_login_success_sets_httponly_cookie(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 200
    assert r.json()["role"] == "SUPER_ADMIN"
    assert "approvals.decide" in r.json()["permissions"]
    assert "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/auth/me").json()["username"] == "admin"


def test_login_wrong_password_rejected_and_audited(client, db):
    login_throttle.clear()
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert r.status_code == 401
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "auth.login_failed")
                      .order_by(AuditLog.id.desc()))
    assert entry is not None and "wrong-password" not in str(entry.details)


def test_login_rate_limited(client):
    login_throttle.clear()
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "admin", "password": "nope-nope"})
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 429
    login_throttle.clear()


def test_me_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_language_persisted(admin_client):
    assert admin_client.put("/api/auth/me/language", json={"language": "en"}).json()["language"] == "en"
    assert admin_client.get("/api/auth/me").json()["language"] == "en"
    assert admin_client.put("/api/auth/me/language", json={"language": "fr"}).status_code == 422
    admin_client.put("/api/auth/me/language", json={"language": "ar"})


def test_logout_all_invalidates_existing_tokens(admin_client):
    token = admin_client.cookies.get("acc_session")
    assert admin_client.post("/api/auth/logout-all").status_code == 204
    r = admin_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


# ---------- secrets ----------
def test_password_hashing():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)
    assert not verify_password("wrong", h)


def test_secret_encryption_roundtrip_and_hint():
    key = "sk-ant-api03-abcdefghijklmnop1234"
    enc = encrypt_secret(key)
    assert key not in enc
    assert decrypt_secret(enc) == key
    assert secret_hint(key) == "…1234"
    assert secret_hint("short") == "…"


def test_redaction():
    s = ("key sk-ant-api03-AAAAAAAAAAAAAAAAAAAA token ghp_BBBBBBBBBBBBBBBBBBBBBBBB "
         "password=hunter2xx https://user:pass@github.com/x Authorization: Bearer abcdefghijklmnopqrstuvwxyz")
    out = redact(s)
    for leaked in ["sk-ant-api03-AAAA", "ghp_BBBB", "hunter2xx", "user:pass@", "abcdefghijklmnopqrstuvwxyz"]:
        assert leaked not in out
    obj = redact_obj({"api_key": "abc123456", "nested": [{"note": "uses sk-ant-api03-CCCCCCCCCCCCCCCCCC"}]})
    assert obj["api_key"] == "[REDACTED]"
    assert "CCCC" not in obj["nested"][0]["note"]


def test_login_throttle_still_limits_when_redis_is_down(monkeypatch):
    from app.security.auth import LoginThrottle

    def down():
        raise ConnectionError("redis down")
    t = LoginThrottle()
    monkeypatch.setattr(LoginThrottle, "_redis", staticmethod(down))
    for _ in range(5):
        assert not t.blocked("u1")
        t.failed("u1")
    assert t.blocked("u1") and not t.blocked("u2")
    t.succeeded("u1")
    assert not t.blocked("u1")
