"""GitHub REST client (GH-02) — repositories, branches, commits, compare/diffs, pull requests, check runs, merges.

Auth is a fine-grained personal access token or a GitHub App installation token, stored encrypted (GH-01) and used
only here, in an Authorization header; it never reaches a prompt, a log or the sandbox. GitHub Enterprise is supported
through `api_base`. Network errors and rate limits are retryable; everything else is a clear, non-retryable error.
Merging is not exposed to agents: `merge_pull` is called only by the Approval Center executor, with the approved
head SHA so GitHub itself refuses the merge if the branch moved.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlsplit

import httpx2 as httpx

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "ai-agent-command-center"}
HTTP_CLIENT: httpx.AsyncClient | None = None  # tests inject a transport


class GitHubError(Exception):
    def __init__(self, status: int, message: str, retryable: bool = False):
        super().__init__(f"GitHub {status}: {message}")
        self.status, self.message, self.retryable = status, message, retryable


def _repo(full_name: str) -> str:
    owner, _, name = full_name.partition("/")
    if not owner or not name or "/" in name:
        raise GitHubError(400, "repository must be owner/name")
    return f"{quote(owner, safe='')}/{quote(name, safe='')}"


class GitHub:
    def __init__(self, token: str, *, api_base: str = API, http_client: httpx.AsyncClient | None = None):
        u = urlsplit(api_base)
        if u.scheme != "https" and u.hostname not in ("localhost", "127.0.0.1"):
            raise GitHubError(400, "GitHub API base must be https")
        self.api = api_base.rstrip("/")
        self._http = http_client or HTTP_CLIENT or httpx.AsyncClient(timeout=30, follow_redirects=False)
        self._own = http_client is None and HTTP_CLIENT is None
        self._auth = {"Authorization": f"Bearer {token}"}

    async def aclose(self) -> None:
        if self._own:
            await self._http.aclose()

    async def _req(self, method: str, path: str, **kw) -> Any:
        try:
            r = await self._http.request(method, f"{self.api}{path}", headers={**HEADERS, **self._auth}, **kw)
        except httpx.TransportError as e:
            raise GitHubError(0, f"unreachable ({type(e).__name__})", retryable=True) from e
        if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or r.status_code == 429):
            raise GitHubError(r.status_code, "rate limited", retryable=True)
        if r.status_code >= 500:
            raise GitHubError(r.status_code, "server error", retryable=True)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", "")
            except ValueError:
                msg = r.text[:200]
            raise GitHubError(r.status_code, msg or "request failed")
        return r.json() if r.content else None

    # ---- read ----
    async def repo(self, full_name: str) -> dict:
        return await self._req("GET", f"/repos/{_repo(full_name)}")

    async def branches(self, full_name: str) -> list[dict]:
        return await self._req("GET", f"/repos/{_repo(full_name)}/branches", params={"per_page": 100})

    async def commits(self, full_name: str, branch: str, limit: int = 30) -> list[dict]:
        return await self._req("GET", f"/repos/{_repo(full_name)}/commits", params={"sha": branch, "per_page": limit})

    async def compare(self, full_name: str, base: str, head: str) -> dict:
        return await self._req("GET", f"/repos/{_repo(full_name)}/compare/{quote(base, safe='')}...{quote(head, safe='')}")

    async def pulls(self, full_name: str, state: str = "open") -> list[dict]:
        return await self._req("GET", f"/repos/{_repo(full_name)}/pulls", params={"state": state, "per_page": 50})

    async def check_runs(self, full_name: str, sha: str) -> dict:
        return await self._req("GET", f"/repos/{_repo(full_name)}/commits/{quote(sha, safe='')}/check-runs")

    # ---- write ----
    async def create_pull(self, full_name: str, *, title: str, head: str, base: str, body: str,
                          draft: bool = False) -> dict:
        return await self._req("POST", f"/repos/{_repo(full_name)}/pulls",
                               json={"title": title, "head": head, "base": base, "body": body, "draft": draft})

    async def merge_pull(self, full_name: str, number: int, *, sha: str, title: str) -> dict:
        """Approval Center only. `sha` makes GitHub refuse the merge (409) if the head moved after approval."""
        return await self._req("PUT", f"/repos/{_repo(full_name)}/pulls/{int(number)}/merge",
                               json={"sha": sha, "merge_method": "merge", "commit_title": title})
