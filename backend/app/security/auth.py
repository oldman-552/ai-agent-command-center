"""Authentication, CSRF, and permission dependencies."""
from __future__ import annotations

import secrets
import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.security.audit import log_activity
from app.security.crypto import create_access_token, decode_access_token, verify_password

COOKIE_NAME = "acc_session"
CSRF_COOKIE = "acc_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

router = APIRouter(prefix="/api/auth", tags=["auth"])

_WINDOW, _MAX_FAILS = 300.0, 5


class LoginThrottle:
    """Failed sign-ins per username within a sliding window, shared by every API replica through Redis (a sorted
    set per username, keyed by a hash so usernames never appear in Redis). If Redis is unreachable it falls back to
    this process's memory: still a limit, though per replica — never a reason to let sign-ins through unchecked."""
    PREFIX = "acc:login_fail:"

    def __init__(self) -> None:
        self._local: dict[str, list[float]] = defaultdict(list)

    @classmethod
    def _key(cls, username: str) -> str:
        import hashlib
        return cls.PREFIX + hashlib.sha256(username.lower().encode()).hexdigest()

    @staticmethod
    def _redis():
        from app.observability.live import client
        return client()

    def blocked(self, username: str) -> bool:
        now = time.time()
        try:
            r, k = self._redis(), self._key(username)
            r.zremrangebyscore(k, 0, now - _WINDOW)
            return int(r.zcard(k)) >= _MAX_FAILS
        except Exception:  # noqa: BLE001 — Redis down: local fallback
            self._local[username] = [t for t in self._local[username] if now - t < _WINDOW]
            return len(self._local[username]) >= _MAX_FAILS

    def failed(self, username: str) -> None:
        now = time.time()
        try:
            r, k = self._redis(), self._key(username)
            p = r.pipeline()
            p.zadd(k, {f"{now}:{secrets.token_hex(4)}": now})
            p.expire(k, int(_WINDOW) + 1)
            p.execute()
        except Exception:  # noqa: BLE001
            self._local[username].append(now)

    def succeeded(self, username: str) -> None:
        self._local.pop(username, None)
        try:
            self._redis().delete(self._key(username))
        except Exception:  # noqa: BLE001, S110 — best effort; the window expires anyway
            pass

    def clear(self) -> None:
        """Tests only."""
        self._local.clear()
        try:
            r = self._redis()
            for k in r.scan_iter(self.PREFIX + "*"):
                r.delete(k)
        except Exception:  # noqa: BLE001, S110 — best effort; the window expires anyway
            pass


login_throttle = LoginThrottle()


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    role_name_ar: str
    role_name_en: str
    language: str
    organization_id: int
    permissions: list[str]
    is_active: bool = True


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, role=u.role.key, role_name_ar=u.role.name_ar,
                   role_name_en=u.role.name_en, language=u.language, organization_id=u.organization_id,
                   permissions=sorted(p.key for p in u.role.permissions), is_active=u.is_active)


class LanguageIn(BaseModel):
    language: str = Field(pattern="^(ar|en)$")


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    token = request.cookies.get(COOKIE_NAME)
    via_cookie = bool(token)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    data = decode_access_token(token) if token else None
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    # CSRF double-submit: cookie-authenticated state-changing requests must echo the CSRF cookie in a header.
    if via_cookie and request.method not in SAFE_METHODS:
        c, h = request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)
        if not c or not h or not secrets.compare_digest(c, h):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf_failed")
    user = db.get(User, int(data["sub"]))
    if not user or not user.is_active or user.token_version != data.get("tv"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    return user


def require(*perms: str):
    """Dependency factory: user must hold every listed permission (server-side RBAC)."""

    def dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        held = {p.key for p in user.role.permissions}
        if not set(perms) <= held:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
        return user

    return dep


CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[Session, Depends(get_db)]


def _set_session(response: Response, user: User) -> None:
    s = get_settings()
    age = s.jwt_expire_minutes * 60
    response.set_cookie(COOKIE_NAME, create_access_token(user.id, user.token_version), httponly=True,
                        samesite="lax", secure=s.cookie_secure, max_age=age, path="/")
    # readable by JS on purpose (double-submit); useless to an attacker on another origin
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), httponly=False, samesite="strict",
                        secure=s.cookie_secure, max_age=age, path="/")


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: DB) -> UserOut:
    if login_throttle.blocked(body.username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts")
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        login_throttle.failed(body.username)
        log_activity(db, "auth.login_failed", category="security", level="warning",
                     organization_id=user.organization_id if user else None,
                     details={"username": body.username}, commit=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
    login_throttle.succeeded(body.username)
    _set_session(response, user)
    log_activity(db, "auth.login", category="security", user_id=user.id, organization_id=user.organization_id,
                 commit=True)
    return user_out(user)


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/logout-all", status_code=204)
def logout_all(user: CurrentUser, response: Response, db: DB) -> None:
    user.token_version += 1
    log_activity(db, "auth.logout_all", category="security", user_id=user.id, organization_id=user.organization_id)
    db.commit()
    logout(response)


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user_out(user)


@router.put("/me/language", response_model=UserOut)
def set_language(body: LanguageIn, user: CurrentUser, db: DB) -> UserOut:
    user.language = body.language
    db.commit()
    return user_out(user)
