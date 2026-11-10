"""Docker sandbox for agent code execution (SEC-04, spec Phase 17, ADR-019).

Every command runs in a fresh, throw-away container:

    --network none            no network unless a caller explicitly allows it (the shell tools never do)
    --cpus / --memory / --memory-swap / --pids-limit / time limit (docker kill on expiry)
    --read-only rootfs, tmpfs /tmp, only the task workspace mounted at /workspace
    --cap-drop ALL, --security-opt no-new-privileges, non-root uid 10001
    a minimal fixed environment: nothing from the platform process (no keys, no DB URL) reaches the container

The workspace must resolve (symlinks included) inside `<workspaces_dir>/<organization>/<project>`, so one project's
sandbox can never mount another project's files or the host. Output is size-capped and secret-redacted.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.security.crypto import redact

log = logging.getLogger("sandbox")
MAX_OUTPUT = 256 * 1024
UID = "10001:10001"
SAFE_ENV = {"HOME": "/tmp", "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "CI": "1"}


class SandboxError(Exception):
    """The sandbox refused or could not run the command (not a failing test — that is a normal result)."""


@dataclass
class Limits:
    cpus: float
    memory: str
    timeout_s: float
    pids: int = 256
    tmp_size: str = "256m"
    network: bool = False


@dataclass
class Result:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    truncated: bool

    def as_dict(self) -> dict:
        return {"exit_code": self.exit_code, "stdout": self.stdout, "stderr": self.stderr, "timed_out": self.timed_out,
                "duration_s": round(self.duration_s, 3), "truncated": self.truncated}


def default_limits() -> Limits:
    s = get_settings()
    return Limits(cpus=s.sandbox_cpu_limit, memory=s.sandbox_memory_limit, timeout_s=float(s.sandbox_timeout_seconds))


def project_root(organization_id: int, project_id: int) -> Path:
    return Path(get_settings().workspaces_dir).resolve() / str(organization_id) / str(project_id)


def ensure_workspace(organization_id: int, project_id: int) -> Path:
    """Create the project's workspace, owned by the sandbox uid so the unprivileged container can write to it.
    (In Docker deployments the backend itself runs as uid 10001; when it runs as root, ownership is set here.)"""
    root = project_root(organization_id, project_id)
    root.mkdir(parents=True, exist_ok=True, mode=0o750)
    uid, gid = (int(x) for x in UID.split(":"))
    if os.geteuid() == 0:
        for p in (root, *root.rglob("*")):
            if p.lstat().st_uid != uid:
                os.chown(p, uid, gid, follow_symlinks=False)
    return root


def resolve_workspace(path: str | os.PathLike, organization_id: int, project_id: int) -> Path:
    """The real path of a workspace, refusing anything outside this project's root (symlinks resolved)."""
    root = project_root(organization_id, project_id)
    real = Path(path).resolve()
    if real != root and root not in real.parents:
        raise SandboxError("workspace outside the project's sandbox root")
    if not real.is_dir():
        raise SandboxError("workspace does not exist")
    return real


def docker_args(name: str, workspace: Path, argv: list[str], limits: Limits, image: str) -> list[str]:
    args = ["docker", "run", "--rm", "--name", name, "--init",
            "--network", "bridge" if limits.network else "none",
            "--cpus", str(limits.cpus), "--memory", limits.memory, "--memory-swap", limits.memory,
            "--pids-limit", str(limits.pids), "--read-only",
            "--tmpfs", f"/tmp:rw,nosuid,nodev,size={limits.tmp_size}",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", UID,
            "--ipc", "none", "--label", "acc.sandbox=1",
            "--mount", f"type=bind,source={workspace},target=/workspace",
            "--workdir", "/workspace"]
    for k, v in SAFE_ENV.items():
        args += ["--env", f"{k}={v}"]
    return [*args, image, *argv]


def _cap(b: bytes) -> tuple[str, bool]:
    return redact(b[:MAX_OUTPUT].decode("utf-8", "replace")), len(b) > MAX_OUTPUT


async def run(workspace: Path, argv: list[str], *, limits: Limits | None = None, image: str | None = None) -> Result:
    limits = limits or default_limits()
    image = image or get_settings().sandbox_image
    name = f"acc-sbx-{uuid.uuid4().hex[:12]}"
    started = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_args(name, workspace, argv, limits, image), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "DOCKER_HOST": os.environ.get("DOCKER_HOST", "")} if
            os.environ.get("DOCKER_HOST") else {"PATH": os.environ.get("PATH", "")})
    except FileNotFoundError as e:
        raise SandboxError("docker is not available") from e
    timed_out = False
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=limits.timeout_s)
    except TimeoutError:
        timed_out = True
        kill = await asyncio.create_subprocess_exec("docker", "kill", name, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await kill.wait()
        out, err = await proc.communicate()
    code = 124 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    if code == 125 and not timed_out:  # docker itself failed (bad image, daemon down): not a command result
        raise SandboxError(f"docker failed: {err.decode('utf-8', 'replace')[:500]}")
    stdout, t1 = _cap(out)
    stderr, t2 = _cap(err)
    return Result(code, stdout, stderr, timed_out, time.monotonic() - started, t1 or t2)
