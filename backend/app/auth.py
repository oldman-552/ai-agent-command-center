from __future__ import annotations

import time
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_activity
from app.config import get_settings
from app.db import get_db
from app.models import User, UserRole
from app.security import create_access_token, decode_access_token, verify_password

COOKIE_NAME = "acc_session"
router = APIRouter(prefix="/api/auth", tags=["auth"])

# Simple brute-force protection: max 5 failures per username per 5 minutes.
_FAILS: dict[str, list[float]] = defaultdict(list)
_WINDOW, _MAX_FAILS = 300.0, 5


def _too_many(username: str) -> bool:
    now = time.monotonic()
    _FAILS[username] = [t for t in _FAILS[username] if now - t < _WINDOW]
    return len(_FAILS[username]) >= _MAX_FAILS


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    language: str

    model_config = {"from_attributes": True}


class LanguageIn(BaseModel):
    language: str = Field(pattern="^(ar|en)$")


def get_current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    token = request.cookies.get(COOKIE_NAME)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    data = decode_access_token(token) if token else None
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    user = db.get(User, int(data["sub"]))
    if not user or not user.is_active or user.token_version != data.get("tv"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not_authenticated")
    return user


def require_super_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != UserRole.SUPER_ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "forbidden")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
SuperAdmin = Annotated[User, Depends(require_super_admin)]
DB = Annotated[Session, Depends(get_db)]


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, response: Response, db: DB) -> User:
    if _too_many(body.username):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too_many_attempts")
    user = db.scalar(select(User).where(User.username == body.username))
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        _FAILS[body.username].append(time.monotonic())
        log_activity(db, "auth.login_failed", category="security", level="warning",
                     details={"username": body.username}, commit=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_credentials")
    _FAILS.pop(body.username, None)
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_access_token(user.id, user.token_version),
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        max_age=s.jwt_expire_minutes * 60,
        path="/",
    )
    log_activity(db, "auth.login", category="security", user_id=user.id, commit=True)
    return user


@router.post("/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


@router.post("/logout-all", status_code=204)
def logout_all(user: CurrentUser, response: Response, db: DB) -> None:
    """Invalidate every session of this user."""
    user.token_version += 1
    log_activity(db, "auth.logout_all", category="security", user_id=user.id)
    db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.put("/me/language", response_model=UserOut)
def set_language(body: LanguageIn, user: CurrentUser, db: DB) -> User:
    user.language = body.language
    db.commit()
    return user
