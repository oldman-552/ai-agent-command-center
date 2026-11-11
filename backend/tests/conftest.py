"""Tests run against a real PostgreSQL test database (TEST_DATABASE_URL).

The schema is rebuilt with the real Alembic migrations, so migrations are tested too.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://acc:acc_dev@localhost:5432/acc_test")
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ["ADMIN_USERNAME"] = "admin"
os.environ.setdefault("EXECUTION_DISPATCH", "inline")  # bus tests (test_bus.py) switch to "bus" explicitly
TEST_AMQP = os.environ.get("TEST_RABBITMQ_URL", "amqp://acc:acc_dev@localhost:5672/acc_test")  # dedicated vhost
os.environ["RABBITMQ_URL"] = TEST_AMQP
os.environ["WORKSPACES_DIR"] = os.environ.get("TEST_WORKSPACES_DIR", "/tmp/acc-test-workspaces")
os.environ.setdefault("SANDBOX_IMAGE", "acc/sandbox:python")
os.environ["WORKFORCE_ATTACHMENTS_DIR"] = os.environ.get("TEST_ATTACHMENTS_DIR", "/tmp/acc-test-attachments")
os.environ["ADMIN_PASSWORD"] = "test-admin-password-1"
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")  # live updates + heartbeats
os.environ.setdefault("LOG_FORMAT", "text")
os.environ.setdefault("BROWSER_SHOW_TEST_PAGE", "true")  # the local test page drives the bridge tests

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from alembic import command
from app.config import get_settings

BACKEND = Path(__file__).resolve().parent.parent
ADMIN_PW = "test-admin-password-1"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    get_settings.cache_clear()
    eng = create_engine(TEST_DB)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    eng.dispose()
    # project ids restart with the fresh schema, so sandbox workspaces from earlier runs must go too
    shutil.rmtree(os.environ["WORKSPACES_DIR"], ignore_errors=True)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
    from app.bootstrap import run_bootstrap
    from app.db import session_factory
    with session_factory()() as s:
        run_bootstrap(s)  # default org (id 1), RBAC, super admin, templates — same as app startup
    yield


@pytest.fixture()
def db():
    from app.db import session_factory

    s = session_factory()()
    yield s
    s.close()


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def login_as(client, username: str, password: str):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    client.headers["x-csrf-token"] = client.cookies["acc_csrf"]  # double-submit, as the browser does
    return client


@pytest.fixture()
def admin_client(client):
    client.headers.pop("x-csrf-token", None)
    return login_as(client, "admin", ADMIN_PW)


@pytest.fixture()
def fake_bridge():
    """A FAKE Arena Local Bridge with four accounts on a real local port (real HTTP; never Arena)."""
    from app.connectivity.gateway import service as gw
    from tests.fakebridge import FakeBridgeServer, four_account_bridge

    gw.HTTP_CLIENTS.pop("arena_local_bridge", None)
    srv = FakeBridgeServer(four_account_bridge()).start()
    yield srv
    srv.stop()


@pytest.fixture()
def make_user(db):
    """Create a user with a given role (optionally in another organization)."""
    from sqlalchemy import select

    from app.models import Organization, Role, User
    from app.security.crypto import hash_password

    def _make(username: str, role_key: str, org_slug: str = "default", password: str = "user-password-123"):
        org = db.scalar(select(Organization).where(Organization.slug == org_slug))
        if not org:
            org = Organization(slug=org_slug, name=org_slug)
            db.add(org)
            db.flush()
        role = db.scalar(select(Role).where(Role.key == role_key))
        u = db.scalar(select(User).where(User.username == username))
        if not u:
            u = User(organization_id=org.id, username=username, password_hash=hash_password(password), role_id=role.id)
            db.add(u)
        db.commit()
        return u

    return _make
