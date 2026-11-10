"""Hardened local git for the development plane (GH-03, GH-04, ADR-021).

Repository content is untrusted (spec Phase 17), so the platform runs git with:
  - a scrubbed environment (no GIT_* variables, no credentials, HOME pointing at an empty directory),
  - hooks disabled (`core.hooksPath=/dev/null`), no fsmonitor, no external diff/textconv/filters,
  - fixed argv built here — agent input only ever appears as file paths or commit message *arguments*.
Protected branches can never be the target of an agent write; the only way into them is `merge_approved()`, which the
Approval Center calls after a human approval.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

SAFE_CONFIG = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "diff.external=",
               "-c", "core.sshCommand=false", "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
               "-c", "commit.gpgsign=false", "-c", "safe.directory=*", "-c", "init.defaultBranch=main",
               "-c", "core.autocrlf=false", "-c", "core.symlinks=false"]
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
PLATFORM = ("AI Agent Command Center", "platform@acc.local")


class GitError(Exception):
    pass


class ProtectedBranch(GitError):
    """An agent tried to write to a protected branch."""


def _env() -> dict[str, str]:
    home = Path("/tmp/acc-git-home")
    home.mkdir(exist_ok=True)
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "LANG": "C.UTF-8",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}


def git(cwd: Path | str, *args: str, check: bool = True, author: tuple[str, str] | None = None) -> str:
    env = _env()
    if author:
        env.update(GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1],
                   GIT_COMMITTER_NAME=PLATFORM[0], GIT_COMMITTER_EMAIL=PLATFORM[1])
    r = subprocess.run(["git", *SAFE_CONFIG, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
                       check=False)
    if check and r.returncode != 0:
        raise GitError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:800]}")
    return r.stdout.strip()


def valid_branch(name: str) -> bool:
    return bool(BRANCH_RE.match(name)) and ".." not in name and not name.startswith(("-", "/")) \
        and not name.endswith((".lock", "/", ".")) and "//" not in name


def assert_not_protected(branch: str, protected: list[str]) -> None:
    if branch in protected or branch in ("HEAD",):
        raise ProtectedBranch(f"branch {branch!r} is protected; agents can only reach it through an approved merge")


def init_repo(path: Path, default_branch: str = "main") -> str:
    """A local project repository with an initial commit (new-project mode). Returns the commit id."""
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        git(path, "init", "-q", "-b", default_branch)
        (path / "README.md").write_text("# Project\n")
        git(path, "add", "README.md")
        git(path, "commit", "-q", "-m", "Initial commit", author=PLATFORM)
    return git(path, "rev-parse", "HEAD")


def head(path: Path) -> str:
    return git(path, "rev-parse", "HEAD")


def current_branch(path: Path) -> str:
    return git(path, "rev-parse", "--abbrev-ref", "HEAD")


def changed_files(worktree: Path, base: str) -> list[str]:
    """Files changed in the worktree relative to `base`, committed or not (including untracked)."""
    committed = git(worktree, "diff", "--name-only", f"{base}...HEAD").splitlines()
    pending = git(worktree, "status", "--porcelain", "-uall", "--no-renames").splitlines()
    return sorted({*committed, *[line[3:] for line in pending if len(line) > 3]})


def merge_conflicts(repo: Path, base_branch: str, branch: str) -> list[str]:
    """Files that would conflict if `branch` were merged into `base_branch` (no working tree changes)."""
    r = subprocess.run(["git", *SAFE_CONFIG, "merge-tree", "--write-tree", "--name-only", "--no-messages",
                        base_branch, branch], cwd=repo, env=_env(), capture_output=True, text=True, timeout=120,
                       check=False)
    if r.returncode == 0:
        return []
    if r.returncode == 1:  # conflicts: first line is the tree id, then the conflicted paths
        return sorted({ln for ln in r.stdout.splitlines()[1:] if ln.strip()})
    raise GitError(f"merge-tree failed: {r.stderr.strip()[:500]}")


def _askpass() -> str:
    """A helper that answers git's credential prompt from the environment, so the token is never in argv/URLs."""
    path = Path("/tmp/acc-git-home/askpass.sh")
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text('#!/bin/sh\ncase "$1" in Username*) echo x-access-token ;; *) echo "$ACC_GIT_TOKEN" ;; esac\n')
        path.chmod(0o700)
    return str(path)


def push_branch(worktree: Path, remote_url: str, branch: str, token: str | None, protected: list[str]) -> None:
    """Push an agent branch (never a protected one) to the remote. https only, except local paths in tests."""
    assert_not_protected(branch, protected)
    if not valid_branch(branch):
        raise GitError("invalid branch")
    if not remote_url.startswith(("https://", "/")):
        raise GitError("remote must be https")
    env = _env()
    if token:
        env.update(GIT_ASKPASS=_askpass(), ACC_GIT_TOKEN=token)
    r = subprocess.run(["git", *SAFE_CONFIG, "-c", "protocol.https.allow=always", "-c", "credential.helper=",
                        "push", "--porcelain", remote_url, f"refs/heads/{branch}:refs/heads/{branch}"],
                       cwd=worktree, env=env, capture_output=True, text=True, timeout=300, check=False)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).replace(token or "\x00", "***")
        raise GitError(f"push failed: {msg.strip()[:500]}")


def clone(dest: Path, remote_url: str, token: str | None) -> str:
    """Clone an existing repository (existing-project mode). https only, except local paths in tests."""
    if not remote_url.startswith(("https://", "/")):
        raise GitError("remote must be https")
    if dest.exists() and any(dest.iterdir()):
        raise GitError("destination is not empty")
    dest.parent.mkdir(parents=True, exist_ok=True)
    env = _env()
    if token:
        env.update(GIT_ASKPASS=_askpass(), ACC_GIT_TOKEN=token)
    r = subprocess.run(["git", *SAFE_CONFIG, "-c", "protocol.https.allow=always", "-c", "credential.helper=",
                        "clone", "--quiet", "--no-recurse-submodules", "--", remote_url, str(dest)],
                       env=env, capture_output=True, text=True, timeout=600, check=False)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).replace(token or "\x00", "***")
        raise GitError(f"clone failed: {msg.strip()[:500]}")
    return git(dest, "rev-parse", "HEAD")
