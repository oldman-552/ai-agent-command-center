"""M8 — GH-01 auth, GH-02 client, GH-05 PRs, GH-06 remote merge via approval, GH-07 webhooks.

GitHub is an in-memory fake behind an HTTP transport (no network here); git pushes go to a real local bare repository.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid

import httpx2 as httpx
import pytest
from sqlalchemy import select

import app.devplane.tools
import app.main  # noqa: F401
from app.devplane import git as g
from app.devplane import github as gh_mod
from app.devplane import service as svc
from app.governance import approvals
from app.models import (
    Agent,
    ApiCredential,
    Approval,
    EvaluationResult,
    ExecutionEvent,
    Project,
    PullRequest,
    Repository,
    Task,
    Worktree,
)
from app.orchestration import scheduler
from app.runtime import contract, service
from app.runtime.engines.mock import MockEngine

GOOD = "github_pat_" + "A" * 30


class FakeGitHub:
    def __init__(self):
        self.pulls: dict[int, dict] = {}
        self.requests: list[tuple[str, str, dict]] = []
        self.heads: dict[str, str] = {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content or b"{}")
        self.requests.append((req.method, req.url.path, body))
        if req.headers.get("authorization") != f"Bearer {GOOD}":
            return httpx.Response(401, json={"message": "Bad credentials"})
        path = req.url.path
        if req.method == "GET" and path.count("/") == 3:  # /repos/o/r
            return httpx.Response(200, json={"full_name": path[7:], "default_branch": "main", "private": True})
        if path.endswith("/branches"):
            return httpx.Response(200, json=[{"name": "main"}])
        if path.endswith("/commits"):
            return httpx.Response(200, json=[{"sha": "abc"}])
        if "/compare/" in path:
            return httpx.Response(200, json={"files": [{"filename": "x.py"}]})
        if path.endswith("/check-runs"):
            return httpx.Response(200, json={"total_count": 0, "check_runs": []})
        if req.method == "POST" and path.endswith("/pulls"):
            n = len(self.pulls) + 1
            self.pulls[n] = {**body, "number": n, "html_url": f"https://github.blesdmm.dns-dynamic.net/{path[7:-6]}/pull/{n}",
                             "state": "open"}
            return httpx.Response(201, json=self.pulls[n])
        if req.method == "GET" and path.endswith("/pulls"):
            return httpx.Response(200, json=list(self.pulls.values()))
        if req.method == "PUT" and path.endswith("/merge"):
            n = int(path.split("/")[-2])
            if body.get("sha") != self.heads.get(self.pulls[n]["head"]):
                return httpx.Response(409, json={"message": "Head branch was modified. Review and try the merge again."})
            self.pulls[n]["state"] = "closed"
            return httpx.Response(200, json={"merged": True, "sha": "merge" + body["sha"][:6]})
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    f = FakeGitHub()
    monkeypatch.setattr(gh_mod, "HTTP_CLIENT", httpx.AsyncClient(transport=httpx.MockTransport(f.handler)))
    monkeypatch.setattr(svc, "REMOTE_BASE", str(tmp_path / "remotes"))
    monkeypatch.setitem(contract._ENGINES, "api", MockEngine)
    return f


def _connect(admin_client, name, **kw):
    return admin_client.post("/api/repositories", json={"full_name": name, "token": GOOD, **kw})


def _setup(admin_client, db, fake, tmp_path):
    name = f"acme/svc-{uuid.uuid4().hex[:6]}"
    r = _connect(admin_client, name, webhook_secret="whsec-" + "x" * 20)
    assert r.status_code == 201, r.text
    bare = tmp_path / "remotes" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    g.git(tmp_path, "init", "-q", "--bare", str(bare))
    p = Project(organization_id=1, name=f"GH {uuid.uuid4().hex[:6]}")
    db.add(p)
    db.commit()
    assert admin_client.put(f"/api/projects/{p.id}/repository", json={"repository_id": r.json()["id"]}).status_code == 200
    a = Agent(organization_id=1, name=f"Dev {uuid.uuid4().hex[:4]}", identifier=f"gh-{uuid.uuid4().hex[:6]}",
              role="backend", state="IDLE", execution_engine="api", security_profile="developer", concurrency_limit=5,
              tool_permissions=["files.write", "git.commit", "pr.create", "repo.read"],
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1})
    t = Task(organization_id=1, project_id=p.id, title="Add health endpoint", status="IN_PROGRESS",
             acceptance_criteria=["GET /health returns ok"], attempts=1)
    db.add_all([a, t])
    db.commit()
    return db.get(Repository, r.json()["id"]), p, a, t, bare


def _tools(db, a, t, steps):
    ex = service.create_execution(db, organization_id=1, agent=a, project_id=t.project_id, task_id=t.id, actor="t",
                                  input={"mock_script": steps})
    db.commit()
    asyncio.run(service.run_execution(ex.id))
    db.expire_all()
    return [e.payload["result"] for e in db.scalars(select(ExecutionEvent).where(
        ExecutionEvent.execution_id == ex.id, ExecutionEvent.type == "tool_result").order_by(ExecutionEvent.seq))]


# ---------------- GH-01 ----------------
def test_token_is_verified_stored_encrypted_and_never_returned(admin_client, client, db, fake, make_user):
    from tests.conftest import login_as
    name = f"acme/{uuid.uuid4().hex[:6]}"
    bad = admin_client.post("/api/repositories", json={"full_name": name, "token": "github_pat_" + "B" * 30})
    assert bad.status_code == 400 and bad.json()["detail"].startswith("github:401")
    r = _connect(admin_client, name)
    assert r.status_code == 201 and GOOD not in r.text and r.json()["token_hint"] == "…" + GOOD[-4:]
    cred = db.scalar(select(ApiCredential).where(ApiCredential.name == f"GitHub {name}"))
    assert GOOD not in cred.encrypted_value and cred.kind == "github"
    assert GOOD not in json.dumps(admin_client.get("/api/repositories").json())
    assert _connect(admin_client, name).status_code == 409
    assert admin_client.post("/api/repositories", json={"full_name": "no-slash", "token": GOOD}).status_code == 422
    make_user("dev-gh", "DEVELOPER")
    client.headers.pop("x-csrf-token", None)
    dev = login_as(client, "dev-gh", "user-password-123")
    assert dev.post("/api/repositories", json={"full_name": "acme/x", "token": GOOD}).status_code == 403


# ---------------- GH-02 ----------------
def test_client_reads_and_maps_errors(fake):
    async def go():
        c = gh_mod.GitHub(GOOD)
        assert (await c.branches("acme/x"))[0]["name"] == "main"
        assert (await c.commits("acme/x", "main"))[0]["sha"] == "abc"
        assert (await c.compare("acme/x", "main", "feature/x"))["files"][0]["filename"] == "x.py"
        assert (await c.check_runs("acme/x", "abc"))["total_count"] == 0
        with pytest.raises(gh_mod.GitHubError) as e:
            await c.repo("bad")
        assert e.value.status == 400
        with pytest.raises(gh_mod.GitHubError) as e:
            await gh_mod.GitHub("nope").repo("acme/x")
        assert e.value.status == 401 and not e.value.retryable

    asyncio.run(go())

    def limited(req):
        return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "rate"})

    def down(req):
        raise httpx.ConnectError("boom")

    async def errors():
        for h, status in ((limited, 403), (down, 0), (lambda r: httpx.Response(502), 502)):
            c = gh_mod.GitHub(GOOD, http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
            with pytest.raises(gh_mod.GitHubError) as e:
                await c.repo("acme/x")
            assert e.value.status == status and e.value.retryable

    asyncio.run(errors())
    with pytest.raises(gh_mod.GitHubError):
        gh_mod.GitHub(GOOD, api_base="http://evil.example")


# ---------------- GH-05 + GH-06 remote ----------------
def test_pr_is_pushed_described_and_merged_only_after_approval(admin_client, db, fake, tmp_path, make_user):
    _repo, _p, a, t, bare = _setup(admin_client, db, fake, tmp_path)
    res = _tools(db, a, t, [
        {"tool": "files.write", "args": {"path": "health.py", "content": "def health():\n    return 'ok'\n"}},
        {"tool": "git.commit", "args": {"message": "feat: health"}},
        {"tool": "pr.create", "args": {}}])
    assert res[2]["number"] == 1, res
    wt = db.scalar(select(Worktree).where(Worktree.task_id == t.id))
    assert g.git(tmp_path, "--git-dir", str(bare), "rev-parse", wt.branch) == wt.head_commit  # pushed
    _method, _path, body = next(r for r in fake.requests if r[0] == "POST")
    assert body["head"] == wt.branch and body["base"] == "main"
    for text in ("GET /health returns ok", "health.py", "Merging requires a human approval", "mock"):
        assert text in body["body"]
    assert g.git(tmp_path, "--git-dir", str(bare), "branch", "--list", "main") == ""  # nothing pushed to main
    pr = db.scalar(select(PullRequest).where(PullRequest.task_id == t.id))
    fake.heads[wt.branch] = wt.head_commit
    ev = EvaluationResult(organization_id=1, task_id=t.id, agent_id=a.id, decision="approve", overall=1.0, scores={})
    db.add(ev)
    db.flush()
    t.status, t.result = "REVIEW", {"evaluation_id": ev.id}
    db.commit()
    scheduler.review(db, t, "approve", "", "user:1")
    db.refresh(t)
    ap = db.get(Approval, t.result["approval_id"])
    assert ap.payload["pull_request_id"] == pr.id and not any(r[0] == "PUT" for r in fake.requests)
    approvals.decide(db, ap, user_id=make_user("gh-approver", "ORGANIZATION_ADMIN").id, decision="approve", reason="")
    db.commit()
    db.refresh(ap)
    db.refresh(t)
    put = next(r for r in fake.requests if r[0] == "PUT")
    assert put[2]["sha"] == wt.head_commit  # GitHub is told exactly which commit was approved
    assert ap.status == "executed" and t.status == "COMPLETED"
    db.refresh(pr)
    assert pr.state == "merged"


def test_github_refuses_merge_when_head_moved(admin_client, db, fake, tmp_path, make_user):
    _repo, _p, a, t, _bare = _setup(admin_client, db, fake, tmp_path)
    _tools(db, a, t, [{"tool": "files.write", "args": {"path": "a.py", "content": "A=1\n"}},
                      {"tool": "git.commit", "args": {"message": "a"}}, {"tool": "pr.create", "args": {}}])
    fake.heads[db.scalar(select(Worktree).where(Worktree.task_id == t.id)).branch] = "someone-else-pushed"
    ev = EvaluationResult(organization_id=1, task_id=t.id, agent_id=a.id, decision="approve", overall=1.0, scores={})
    db.add(ev)
    db.flush()
    t.status, t.result = "REVIEW", {"evaluation_id": ev.id}
    db.commit()
    scheduler.review(db, t, "approve", "", "user:1")
    db.refresh(t)
    ap = db.get(Approval, t.result["approval_id"])
    approvals.decide(db, ap, user_id=make_user("gh-approver2", "ORGANIZATION_ADMIN").id, decision="approve", reason="")
    db.commit()
    db.refresh(ap)
    db.refresh(t)
    assert ap.status == "approved" and "Head branch was modified" in ap.evidence[-1]["error"]
    assert t.status == "READY"


def test_push_never_targets_a_protected_branch(tmp_path):
    repo = tmp_path / "r"
    g.init_repo(repo)
    with pytest.raises(g.ProtectedBranch):
        g.push_branch(repo, str(tmp_path / "remote.git"), "main", None, ["main"])
    with pytest.raises(g.GitError):
        g.push_branch(repo, "git@github.com:acme/x.git", "feature", None, ["main"])  # ssh/other schemes refused


# ---------------- GH-07 ----------------
def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhooks_are_signed_deduplicated_and_update_checks(admin_client, client, db, fake, tmp_path):
    repo, _p, a, t, _bare = _setup(admin_client, db, fake, tmp_path)
    _tools(db, a, t, [{"tool": "files.write", "args": {"path": "b.py", "content": "B=1\n"}},
                      {"tool": "git.commit", "args": {"message": "b"}}, {"tool": "pr.create", "args": {}}])
    wt = db.scalar(select(Worktree).where(Worktree.task_id == t.id))
    secret = "whsec-" + "x" * 20
    body = json.dumps({"check_run": {"head_sha": wt.head_commit, "name": "ci/tests", "status": "completed",
                                     "conclusion": "success"}}).encode()
    url = f"/api/github/webhook/{repo.id}"
    h = {"x-github-event": "check_run", "x-github-delivery": str(uuid.uuid4()), "content-type": "application/json"}
    assert client.post(url, content=body, headers={**h, "x-hub-signature-256": _sign("wrong", body)}).status_code == 401
    assert client.post(url, content=body, headers=h).status_code == 401  # unsigned
    assert client.post("/api/github/webhook/999999", content=body,
                       headers={**h, "x-hub-signature-256": _sign(secret, body)}).status_code == 401
    ok = client.post(url, content=body, headers={**h, "x-hub-signature-256": _sign(secret, body)})
    assert ok.json() == {"status": "ok", "result": "check_run:1"}
    again = client.post(url, content=body, headers={**h, "x-hub-signature-256": _sign(secret, body)})
    assert again.json() == {"status": "duplicate"}  # replayed delivery
    pr = db.scalar(select(PullRequest).where(PullRequest.task_id == t.id))
    db.refresh(pr)
    assert pr.checks["ci/tests"]["conclusion"] == "success"
    merged = json.dumps({"pull_request": {"number": pr.number, "merged": True, "state": "closed"}}).encode()
    r = client.post(url, content=merged, headers={"x-github-event": "pull_request", "x-github-delivery": str(uuid.uuid4()),
                                                  "x-hub-signature-256": _sign(secret, merged)})
    assert r.json()["result"] == "pull_request:merged"
    assert admin_client.get(f"/api/pull-requests?repository_id={repo.id}").json()[0]["checks"]["ci/tests"]
