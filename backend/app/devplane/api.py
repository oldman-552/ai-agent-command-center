"""Repositories, worktrees, pull requests and GitHub webhooks API (GH-01, GH-07, GH-08)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.devplane import service as svc
from app.devplane.git import valid_branch
from app.devplane.github import GitHub, GitHubError
from app.models import ApiCredential, Project, PullRequest, Repository, User, Worktree
from app.security.audit import log_activity
from app.security.auth import DB, require
from app.security.crypto import decrypt_secret, encrypt_secret
from app.security.tenancy import get_scoped

router = APIRouter(prefix="/api", tags=["github"])
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


def _hint(secret: str) -> str:
    return "…" + secret[-4:]


def repo_out(db, r: Repository) -> dict:
    cred = db.get(ApiCredential, r.credential_id) if r.credential_id else None
    return {"id": r.id, "full_name": r.full_name, "default_branch": r.default_branch,
            "protected_branches": r.protected_branches, "api_base": r.api_base, "is_private": r.is_private,
            "token_hint": cred.hint if cred else None, "webhook_configured": r.webhook_credential_id is not None,
            "created_at": r.created_at}


class RepoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str
    token: str = Field(min_length=10, max_length=512)
    default_branch: str = "main"
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"], max_length=50)
    api_base: str = "https://api.github.com"
    webhook_secret: str | None = Field(default=None, min_length=16, max_length=256)
    verify: bool = True

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("must be owner/name")
        return v

    @field_validator("default_branch")
    @classmethod
    def _branch(cls, v: str) -> str:
        if not valid_branch(v):
            raise ValueError("invalid branch")
        return v


@router.get("/repositories")
def list_repos(db: DB, user: User = Depends(require("github.read"))) -> list[dict]:
    rows = db.scalars(select(Repository).where(Repository.organization_id == user.organization_id).order_by(Repository.id))
    return [repo_out(db, r) for r in rows]


@router.post("/repositories", status_code=201)
async def connect_repo(body: RepoIn, db: DB, user: User = Depends(require("credentials.manage"))) -> dict:
    """GH-01: the token is verified against GitHub, then stored encrypted; only a 4-character hint is ever returned."""
    if db.scalar(select(Repository.id).where(Repository.organization_id == user.organization_id,
                                             Repository.full_name == body.full_name)):
        raise HTTPException(409, "repository_exists")
    info: dict = {}
    if body.verify:
        gh = GitHub(body.token, api_base=body.api_base)
        try:
            info = await gh.repo(body.full_name)
        except GitHubError as e:
            raise HTTPException(400 if e.status in (401, 403, 404) else 502, f"github:{e.status}:{e.message}") from e
        finally:
            await gh.aclose()
    cred = ApiCredential(organization_id=user.organization_id, name=f"GitHub {body.full_name}", kind="github",
                         encrypted_value=encrypt_secret(body.token), hint=_hint(body.token),
                         created_by_user_id=user.id)
    db.add(cred)
    db.flush()
    hook = None
    if body.webhook_secret:
        hook = ApiCredential(organization_id=user.organization_id, name=f"GitHub webhook {body.full_name}",
                             kind="github_webhook", encrypted_value=encrypt_secret(body.webhook_secret),
                             hint=_hint(body.webhook_secret), created_by_user_id=user.id)
        db.add(hook)
        db.flush()
    r = Repository(organization_id=user.organization_id, full_name=body.full_name,
                   default_branch=info.get("default_branch", body.default_branch),
                   protected_branches=sorted({*body.protected_branches, info.get("default_branch", body.default_branch)}),
                   is_private=bool(info.get("private", True)), credential_id=cred.id, api_base=body.api_base,
                   webhook_credential_id=hook.id if hook else None)
    db.add(r)
    db.flush()
    log_activity(db, "github.repository_connected", category="security", organization_id=user.organization_id,
                 user_id=user.id, details={"repository": body.full_name, "verified": body.verify})
    db.commit()
    return repo_out(db, r)


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repository_id: int | None


@router.put("/projects/{project_id}/repository")
def link_project(project_id: int, body: LinkIn, db: DB, user: User = Depends(require("github.manage"))) -> dict:
    p = get_scoped(db, Project, project_id, user)
    if body.repository_id is not None:
        r = get_scoped(db, Repository, body.repository_id, user)
        p.repository_id, p.default_branch = r.id, r.default_branch
    else:
        p.repository_id = None
    log_activity(db, "github.project_linked", category="devplane", organization_id=user.organization_id,
                 user_id=user.id, project_id=p.id, details={"repository_id": body.repository_id})
    db.commit()
    return {"project_id": p.id, "repository_id": p.repository_id, "default_branch": p.default_branch}


@router.get("/worktrees")
def list_worktrees(db: DB, user: User = Depends(require("github.read")), project_id: int | None = None) -> list[dict]:
    stmt = select(Worktree).where(Worktree.organization_id == user.organization_id)
    if project_id:
        stmt = stmt.where(Worktree.project_id == project_id)
    return [{"id": w.id, "project_id": w.project_id, "task_id": w.task_id, "agent_id": w.agent_id, "branch": w.branch,
             "base_branch": w.base_branch, "head_commit": w.head_commit, "status": w.status,
             "modified_files": w.modified_files, "conflicts": w.conflicts, "commits": w.commits,
             "merged_at": w.merged_at, "updated_at": w.updated_at}
            for w in db.scalars(stmt.order_by(Worktree.id.desc()).limit(200))]


@router.get("/pull-requests")
def list_pulls(db: DB, user: User = Depends(require("github.read")), repository_id: int | None = None) -> list[dict]:
    stmt = select(PullRequest).where(PullRequest.organization_id == user.organization_id)
    if repository_id:
        stmt = stmt.where(PullRequest.repository_id == repository_id)
    return [{"id": p.id, "repository_id": p.repository_id, "task_id": p.task_id, "number": p.number, "url": p.url,
             "title": p.title, "head_branch": p.head_branch, "base_branch": p.base_branch, "state": p.state,
             "checks": p.checks, "created_at": p.created_at} for p in db.scalars(stmt.order_by(PullRequest.id.desc()))]


@router.post("/github/webhook/{repository_id}")
async def webhook(repository_id: int, request: Request, db: DB) -> dict:
    """GH-07: unauthenticated by session, authenticated by the HMAC signature; each delivery is applied once."""
    body = await request.body()
    repo = db.get(Repository, repository_id)
    hook = db.get(ApiCredential, repo.webhook_credential_id) if repo and repo.webhook_credential_id else None
    if hook is None or not svc.verify_signature(decrypt_secret(hook.encrypted_value), body,
                                                request.headers.get("x-hub-signature-256")):
        raise HTTPException(401, "invalid_signature")  # same answer for unknown repositories: nothing to probe
    delivery = request.headers.get("x-github-delivery", "")
    if not delivery:
        raise HTTPException(400, "missing_delivery_id")
    if not svc.first_delivery(db, delivery):
        db.commit()
        return {"status": "duplicate"}
    try:
        payload = json.loads(body or b"{}")
    except ValueError as e:
        raise HTTPException(400, "invalid_json") from e
    result = svc.apply_event(db, repo, request.headers.get("x-github-event", ""), payload)
    log_activity(db, "github.webhook", category="devplane", organization_id=repo.organization_id,
                 details={"event": request.headers.get("x-github-event"), "delivery": delivery, "result": result})
    db.commit()
    return {"status": "ok", "result": result}
