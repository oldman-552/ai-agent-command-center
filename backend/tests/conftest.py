"""Tests run against a real PostgreSQL test database (TEST_DATABASE_URL).

The schema is rebuilt with the real Alembic migrations, so migrations are tested too.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://acc:acc_dev@localhost:5432/acc_test")
os.environ["DATABASE_URL"] = TEST_DB
os.environ.setdefault("JWT_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "test-admin-password-1"

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
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")
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


@pytest.fixture()
def admin_client(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return client
