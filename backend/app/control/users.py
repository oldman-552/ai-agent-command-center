"""Users and roles administration. No-escalation rule: a caller may only assign roles whose permissions
are a subset of their own (spec Phase 22; SEC-01/SEC-02)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import Role, User
from app.security.audit import log_activity
from app.security.auth import DB, UserOut, require, user_out
from app.security.crypto import hash_password
from app.security.tenancy import get_scoped, scoped

router = APIRouter(prefix="/api", tags=["users"])


class RoleOut(BaseModel):
    key: str
    name_ar: str
    name_en: str
    permissions: list[str]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: str
    language: str = Field(default="ar", pattern="^(ar|en)$")


class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None


def _assignable(db, caller: User, role_key: str) -> Role:
    role = db.scalar(select(Role).where(Role.key == role_key))
    if not role:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown_role")
    mine = {p.key for p in caller.role.permissions}
    if not {p.key for p in role.permissions} <= mine:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "cannot_grant_higher_privileges")
    return role


@router.get("/roles", response_model=list[RoleOut])
def list_roles(db: DB, _: User = Depends(require("users.manage"))) -> list[RoleOut]:
    return [RoleOut(key=r.key, name_ar=r.name_ar, name_en=r.name_en, permissions=sorted(p.key for p in r.permissions))
            for r in db.scalars(select(Role).order_by(Role.id))]


@router.get("/users", response_model=list[UserOut])
def list_users(db: DB, caller: User = Depends(require("users.manage"))) -> list[UserOut]:
    return [user_out(u) for u in db.scalars(scoped(select(User), User, caller).order_by(User.id))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: DB, caller: User = Depends(require("users.manage"))) -> UserOut:
    role = _assignable(db, caller, body.role)
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "username_taken")
    u = User(organization_id=caller.organization_id, username=body.username,
             password_hash=hash_password(body.password), role_id=role.id, language=body.language)
    db.add(u)
    db.flush()
    log_activity(db, "users.created", category="security", user_id=caller.id, organization_id=caller.organization_id,
                 details={"target_user_id": u.id, "role": role.key})
    db.commit()
    db.refresh(u)
    return user_out(u)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserPatch, db: DB, caller: User = Depends(require("users.manage"))) -> UserOut:
    u = get_scoped(db, User, user_id, caller)
    if u.id == caller.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot_modify_self")
    # cannot manage someone who holds permissions the caller lacks
    _assignable(db, caller, u.role.key)
    changes = {}
    if body.role is not None:
        u.role_id = _assignable(db, caller, body.role).id
        u.token_version += 1  # force re-login with new rights
        changes["role"] = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        u.token_version += 1
        changes["is_active"] = body.is_active
    log_activity(db, "users.updated", category="security", user_id=caller.id, organization_id=caller.organization_id,
                 details={"target_user_id": u.id, **changes})
    db.commit()
    db.refresh(u)
    return user_out(u)
