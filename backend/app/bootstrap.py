"""Idempotent first-start setup: Super Admin, default agent roles, Mock provider."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_activity
from app.config import get_settings
from app.models import AgentAccessLevel as L
from app.models import AgentProvider, AgentRole, User, UserRole
from app.security import hash_password

log = logging.getLogger(__name__)

# key, Arabic name, English name, access level, default tools
DEFAULT_ROLES: list[tuple[str, str, str, L, list[str]]] = [
    ("manager", "الوكيل المدير", "Manager Agent", L.MANAGER, ["repo.read", "tasks.plan", "tasks.assign", "pr.review"]),
    ("architect", "مهندس البرمجيات", "Software Architect", L.DEVELOPER, ["repo.read", "files.write"]),
    ("backend", "مطور الواجهة الخلفية", "Backend Developer", L.DEVELOPER, ["repo.read", "files.write", "shell.test", "git.branch", "pr.create"]),
    ("frontend", "مطور الواجهة الأمامية", "Frontend Developer", L.DEVELOPER, ["repo.read", "files.write", "shell.test", "git.branch", "pr.create"]),
    ("database", "مهندس قواعد البيانات", "Database Engineer", L.DEVELOPER, ["repo.read", "files.write", "shell.test", "git.branch", "pr.create"]),
    ("devops", "مهندس DevOps", "DevOps Engineer", L.DEVELOPER, ["repo.read", "files.write", "shell.build", "git.branch", "pr.create"]),
    ("qa", "مختبر الجودة", "QA Tester", L.DEVELOPER, ["repo.read", "files.write", "shell.test"]),
    ("security", "مراجع الأمان", "Security Reviewer", L.REVIEWER, ["repo.read", "pr.review"]),
    ("code_reviewer", "مراجع الشيفرة", "Code Reviewer", L.REVIEWER, ["repo.read", "pr.review"]),
    ("research", "وكيل البحث", "Research Agent", L.READ_ONLY, ["repo.read"]),
]


def ensure_super_admin(db: Session) -> User | None:
    s = get_settings()
    existing = db.scalar(select(User).where(User.role == UserRole.SUPER_ADMIN.value))
    if existing:
        return existing
    if not s.admin_password:
        log.error("No Super Admin exists and ADMIN_PASSWORD is empty — set it in .env")
        return None
    user = User(
        username=s.admin_username,
        password_hash=hash_password(s.admin_password),
        role=UserRole.SUPER_ADMIN.value,
        language="ar",
    )
    db.add(user)
    db.flush()
    log_activity(db, "bootstrap.super_admin_created", category="security", user_id=user.id)
    return user


def seed_roles(db: Session) -> int:
    created = 0
    for key, ar, en, level, tools in DEFAULT_ROLES:
        if db.scalar(select(AgentRole).where(AgentRole.key == key)):
            continue
        db.add(AgentRole(key=key, name_ar=ar, name_en=en, access_level=level.value,
                         default_tools=tools, is_system=True))
        created += 1
    return created


def seed_mock_provider(db: Session) -> None:
    if db.scalar(select(AgentProvider).where(AgentProvider.kind == "mock")):
        return
    db.add(AgentProvider(
        name="Mock Provider (TEST ONLY — not real AI)",
        kind="mock",
        is_mock=True,
        enabled=True,
        models=["mock-model"],
        pricing={"mock-model": {"input": 0, "output": 0}},
        connection_status="ok",
    ))


def run_bootstrap(db: Session) -> None:
    ensure_super_admin(db)
    seed_roles(db)
    seed_mock_provider(db)
    db.commit()
