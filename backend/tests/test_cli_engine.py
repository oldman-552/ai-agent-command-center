"""EE-02 — sandboxed CLI execution engine. The CLIs themselves are not installed here; the engine is exercised with a
fake runner (argv, environment, parsing, errors) and the secret-passing path with a real Docker sandbox."""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

import app.main  # noqa: F401
from app.models import Agent, ApiCredential, Execution, Project
from app.runtime import service
from app.runtime.engines import cli
from app.sandbox import runner
from app.security.crypto import encrypt_secret

KEY = "sk-cli-test-0123456789abcdef"


class FakeRunner:
    def __init__(self, stdout="", code=0, timed_out=False):
        self.calls, self.stdout, self.code, self.timed_out = [], stdout, code, timed_out

    async def __call__(self, workspace, argv, *, limits, image, secret_env):
        self.calls.append({"workspace": workspace, "argv": argv, "limits": limits, "image": image, "env": secret_env})
        return runner.Result(self.code, self.stdout, "boom" if self.code else "", self.timed_out, 0.1, False)


def _agent(db, cli_key, **pol):
    p = Project(organization_id=1, name=f"CLI {uuid.uuid4().hex[:6]}")
    cred = ApiCredential(organization_id=1, name=f"cli {uuid.uuid4().hex[:6]}", kind="api_key",
                         encrypted_value=encrypt_secret(KEY), hint="cdef")
    db.add_all([p, cred])
    db.flush()
    a = Agent(organization_id=1, name=f"C {uuid.uuid4().hex[:5]}", identifier=f"cli-{uuid.uuid4().hex[:8]}",
              role="backend", state="IDLE", execution_engine="cli", timeout_seconds=120,
              retry_policy={"max_attempts": 1, "backoff_seconds": 0, "backoff_multiplier": 1},
              context_policy={"cli": cli_key, "cli_credential_id": cred.id, **pol})
    db.add(a)
    db.commit()
    return a, p


def _run(db, a, p, prompt="fix the bug"):
    ex = service.create_execution(db, organization_id=1, agent=a, input={"prompt": prompt}, actor="test",
                                  project_id=p.id)
    db.commit()
    asyncio.run(service.run_execution(ex.id))
    db.expire_all()
    return db.get(Execution, ex.id)


def test_claude_code_argv_secret_env_and_json_output(db, monkeypatch):
    fake = FakeRunner(json.dumps({"type": "result", "result": "Fixed it.", "is_error": False,
                                  "total_cost_usd": 0.01, "usage": {"input_tokens": 12, "output_tokens": 5}}))
    monkeypatch.setattr(cli, "RUNNER", fake)
    a, p = _agent(db, "claude_code", cli_model="claude-sonnet-4-5")
    ex = _run(db, a, p)
    assert ex.status == "COMPLETED", ex.error
    assert ex.output["text"] == "Fixed it." and ex.input_tokens == 12 and ex.output_tokens == 5
    assert ex.provider == "cli:claude_code" and ex.cost_usd is None  # CLI-reported cost is not platform cost
    call = fake.calls[0]
    assert call["argv"] == ["claude", "--bare", "-p", "fix the bug", "--output-format", "json",
                            "--permission-mode", "acceptEdits", "--model", "claude-sonnet-4-5"]
    assert call["env"] == {"ANTHROPIC_API_KEY": KEY} and KEY not in json.dumps(call["argv"])
    assert call["limits"].network is True and call["limits"].timeout_s == 120
    assert call["image"] == "acc/cli:latest" and str(call["workspace"]).endswith(f"/1/{p.id}")


@pytest.mark.parametrize(("key", "stdout", "argv0", "text"), [
    ("codex", "Done: tests pass\n", ["codex", "exec", "--sandbox", "workspace-write", "--cd", "/workspace"],
     "Done: tests pass"),
    ("gemini", json.dumps({"response": "All good", "stats": {"models": {}}}), ["gemini", "-p"], "All good"),
])
def test_other_clis(db, monkeypatch, key, stdout, argv0, text):
    fake = FakeRunner(stdout)
    monkeypatch.setattr(cli, "RUNNER", fake)
    a, p = _agent(db, key)
    ex = _run(db, a, p)
    assert ex.status == "COMPLETED", ex.error
    assert ex.output["text"] == text and fake.calls[0]["argv"][:len(argv0)] == argv0
    assert set(fake.calls[0]["env"]) == {cli.SPECS[key].key_env}


def test_opencode_needs_the_provider_key_variable(db, monkeypatch):
    fake = FakeRunner("ok")
    monkeypatch.setattr(cli, "RUNNER", fake)
    a, p = _agent(db, "opencode", cli_model="anthropic/claude-sonnet-4-5")
    assert "cli_key_env" in (_run(db, a, p).error or "")
    a, p = _agent(db, "opencode", cli_model="anthropic/claude-sonnet-4-5", cli_key_env="ANTHROPIC_API_KEY")
    ex = _run(db, a, p)
    assert ex.status == "COMPLETED" and fake.calls[-1]["argv"] == ["opencode", "run", "--model",
                                                                  "anthropic/claude-sonnet-4-5", "fix the bug"]
    a, p = _agent(db, "opencode", cli_key_env="PATH")  # cannot smuggle arbitrary variables
    assert _run(db, a, p).status == "FAILED"


@pytest.mark.parametrize(("runner_kw", "msg"), [
    ({"code": 127}, "not installed"), ({"code": 2}, "exited with 2"), ({"timed_out": True, "code": 124}, "timed out"),
    ({"stdout": "not json"}, "could not read"),
])
def test_cli_failures_are_explicit(db, monkeypatch, runner_kw, msg):
    monkeypatch.setattr(cli, "RUNNER", FakeRunner(**runner_kw))
    a, p = _agent(db, "claude_code")
    ex = _run(db, a, p)
    assert ex.status == "FAILED" and msg in (ex.error or "")


def test_no_credential_no_subscription_fallback(db, monkeypatch):
    monkeypatch.setattr(cli, "RUNNER", FakeRunner("x"))
    a, p = _agent(db, "claude_code")
    a.context_policy = {"cli": "claude_code"}
    db.commit()
    ex = _run(db, a, p)
    assert ex.status == "FAILED" and "subscriptions" in ex.error


def test_catalog_lists_verified_flags(admin_client):
    r = admin_client.get("/api/runtime/clis").json()
    assert {x["key"] for x in r} == {"claude_code", "codex", "gemini", "opencode"}
    assert all(x["docs"].startswith("https://") and x["verified_flags"] for x in r)


def test_secrets_reach_the_container_by_name_only(tmp_path):
    """Real Docker: the value is visible inside the container, never in docker's argv, and is masked in output."""
    import subprocess
    if subprocess.run(["docker", "info"], check=False, capture_output=True).returncode != 0:
        pytest.skip("docker daemon not available")
    args = runner.docker_args("x", tmp_path, ["true"], runner.default_limits(), "img", ("ACC_TEST_SECRET",))
    assert "ACC_TEST_SECRET" in args and not any("s3cr3t" in a for a in args)
    ws = tmp_path / "ws"
    ws.mkdir()
    code = "import os;print(os.environ['ACC_TEST_SECRET'][:3], os.environ['ACC_TEST_SECRET'])"
    res = asyncio.run(runner.run(ws, ["python3", "-c", code], secret_env={"ACC_TEST_SECRET": "s3cr3t-val"}))
    assert res.exit_code == 0, res.stderr
    assert res.stdout.strip() == "s3c ***"  # reached the process; masked in what we record
    with pytest.raises(runner.SandboxError):
        asyncio.run(runner.run(ws, ["true"], secret_env={"PATH": "/evil"}))
