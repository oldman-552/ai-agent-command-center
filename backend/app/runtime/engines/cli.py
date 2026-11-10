"""`cli` execution engine (EE-02, ADR-029): run a coding CLI non-interactively inside the Docker sandbox.

The agent chooses the CLI in `context_policy.cli` and the platform credential holding its API key in
`context_policy.cli_credential_id`. Each run is a fresh container from `CLI_SANDBOX_IMAGE` with only the task's
worktree mounted at /workspace, non-root, read-only rootfs, no capabilities, resource and time limits — and network
access, because the CLI must reach its model API. The key is passed as an environment variable by name (never in
argv), and removed from any output.

Only flags confirmed in each tool's documentation are used (see docs/PROVIDER_INTEGRATION.md for sources and dates).
Flags that could not be confirmed are not used; where only a mirror of the docs was reachable that is stated.
Subscriptions (Claude Pro/Max, ChatGPT Plus, Google One…) are never used: these CLIs run with API keys only.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import session_factory
from app.models import Agent, ApiCredential, Execution, Provider
from app.runtime.contract import EngineContext, EngineEvent, RetryableError, register_engine
from app.sandbox import runner
from app.security.crypto import decrypt_secret


@dataclass(frozen=True)
class CliSpec:
    key: str
    name: str
    binary: str
    key_env: str | None                      # API key variable; None = taken from context_policy.cli_key_env
    build: Callable[[str, str | None], list[str]]
    parse: Callable[[str], tuple[str, dict[str, Any]]]
    docs: str
    verified: tuple[str, ...] = field(default=())   # flags confirmed in the official docs
    caveats: str = ""
    key_aliases: tuple[str, ...] = ()        # the same key also passed under these names
    # project-level configuration the CLI would load from the workspace (it can widen tool approvals or add
    # commands/MCP servers and so reach the API key); refused unless context_policy.cli_allow_project_config
    project_config: tuple[str, ...] = ()
    provider_kinds: tuple[str, ...] = ()     # provider kinds whose API key this CLI accepts (empty = any)


def _plain(stdout: str) -> tuple[str, dict[str, Any]]:
    return stdout.strip(), {}


def _claude(stdout: str) -> tuple[str, dict[str, Any]]:
    data = json.loads(stdout.strip().splitlines()[-1])  # `--output-format json`: one JSON object
    if data.get("is_error"):
        raise RuntimeError(f"claude reported an error: {str(data.get('result'))[:500]}")
    u = data.get("usage") or {}
    return str(data.get("result", "")), {"input_tokens": int(u.get("input_tokens", 0) or 0),
                                         "output_tokens": int(u.get("output_tokens", 0) or 0),
                                         "cli_reported_cost_usd": data.get("total_cost_usd")}


def _gemini(stdout: str) -> tuple[str, dict[str, Any]]:
    data = json.loads(stdout)  # `--output-format json`: {"response", "stats", "error"?}
    if data.get("error"):
        raise RuntimeError(f"gemini reported an error: {str(data['error'])[:500]}")
    return str(data.get("response", "")), {"cli_stats": data.get("stats")}


SPECS: dict[str, CliSpec] = {s.key: s for s in (
    CliSpec("claude_code", "Claude Code", "claude", "ANTHROPIC_API_KEY",
            lambda p, m: ["claude", "--bare", "-p", p, "--output-format", "json",
                          "--permission-mode", "acceptEdits", *(["--model", m] if m else [])],
            _claude, "https://code.claude.com/docs/en/headless",
            verified=("--bare", "-p", "--output-format json", "--permission-mode acceptEdits", "--model",
                      "ANTHROPIC_API_KEY"),
            caveats="Flags checked against `claude --help` of 2.1.322 and the CLI reference (2026-11-10).",
            provider_kinds=("anthropic",)),
    CliSpec("codex", "OpenAI Codex CLI", "codex", "OPENAI_API_KEY",
            lambda p, m: ["codex", "exec", "--sandbox", "workspace-write", "--cd", "/workspace",
                          "--skip-git-repo-check", *(["--model", m] if m else []), p],
            _plain, "https://developers.openai.com/codex/noninteractive",
            verified=("exec", "--sandbox workspace-write", "--cd", "--skip-git-repo-check", "--model"),
            caveats="Flags checked against `codex exec --help` of codex-cli 0.167.0 (2026-11-10). The binary reads "
                    "both OPENAI_API_KEY and CODEX_API_KEY, so the key is passed under both names; the final "
                    "message is read from stdout.",
            key_aliases=("CODEX_API_KEY",), project_config=(".codex",), provider_kinds=("openai",)),
    CliSpec("gemini", "Gemini CLI", "gemini", "GEMINI_API_KEY",
            lambda p, m: ["gemini", "-p", p, "--output-format", "json", "--skip-trust",
                          *(["--model", m] if m else [])],
            _gemini, "https://www.geminicli.com/docs/cli/headless",
            verified=("-p", "--output-format json", "--skip-trust", "--model", "GEMINI_API_KEY"),
            caveats="Flags checked against `gemini --help` of 0.67.0 and GEMINI_API_KEY in its package (2026-11-10). "
                    "Headless runs refuse an untrusted folder, so the workspace is trusted with --skip-trust; the "
                    "engine refuses workspaces with .gemini/ configuration instead. Tool approvals stay at the "
                    "default (no --yolo).",
            project_config=(".gemini",), provider_kinds=("google",)),
    CliSpec("opencode", "OpenCode", "opencode", None,
            lambda p, m: ["opencode", "run", *(["--model", m] if m else []), p],
            _plain, "https://opencode.ai/docs/cli",
            verified=("run", "--model provider/model"),  # `opencode run --help`, 1.19.5
            caveats="The API key variable depends on the provider (set context_policy.cli_key_env); the default "
                    "formatted output is used because the JSON event schema is not documented.",
            project_config=("opencode.json", "opencode.jsonc", ".opencode")),
)}


def public() -> list[dict[str, Any]]:
    return [{"key": s.key, "name": s.name, "binary": s.binary, "key_env": s.key_env, "docs": s.docs,
             "provider_kinds": list(s.provider_kinds), "project_config": list(s.project_config),
             "verified_flags": list(s.verified), "caveats": s.caveats} for s in SPECS.values()]


RUNNER = runner.run  # tests replace this


class CliEngine:
    kind = "cli"
    is_mock = False

    async def run(self, ctx: EngineContext) -> AsyncIterator[EngineEvent]:
        from app.sandbox.tools import _workspace_for
        pol = ctx.agent.get("context_policy") or {}
        spec = SPECS.get(str(pol.get("cli", "")))
        if spec is None:
            raise RuntimeError(f"unknown CLI {pol.get('cli')!r}; choose one of {sorted(SPECS)}")
        key_env = spec.key_env or str(pol.get("cli_key_env", ""))
        if not key_env.isidentifier() or not key_env.endswith(("_API_KEY", "_KEY", "_TOKEN")):
            raise RuntimeError("context_policy.cli_key_env must name the provider's API key variable")
        with session_factory()() as db:
            ex = db.get(Execution, ctx.execution_id)
            agent = db.get(Agent, ex.agent_id)
            cred = db.get(ApiCredential, int(pol["cli_credential_id"])) if pol.get("cli_credential_id") else None
            if cred is None and agent.provider_id:  # the usual case: the API key of the agent's own provider
                prov = db.get(Provider, agent.provider_id)
                if prov is not None and prov.organization_id == agent.organization_id:
                    if spec.provider_kinds and prov.kind not in spec.provider_kinds:
                        raise RuntimeError(f"{spec.name} needs a {' or '.join(spec.provider_kinds)} API key; the agent's "
                                           f"provider {prov.name!r} is {prov.kind}")
                    cred = prov.credential
            if cred is None or cred.organization_id != agent.organization_id:
                raise RuntimeError("the agent has no API key for its CLI (give the agent a provider with an API key)"
                                   " — CLIs run with API keys, never with consumer subscriptions")
            secret = decrypt_secret(cred.encrypted_value)
            if ex.project_id is None:
                raise RuntimeError("CLI executions need a project workspace")
            model = pol.get("cli_model") or agent.model
            ex.provider, ex.model = f"cli:{spec.key}", model or "default"
            db.commit()
            snapshot = Execution(id=ex.id, organization_id=ex.organization_id, project_id=ex.project_id,
                                 task_id=ex.task_id, agent_id=ex.agent_id)
            timeout = float(agent.timeout_seconds or get_settings().sandbox_timeout_seconds)
        workspace = Path(_workspace_for(snapshot))
        found = [c for c in spec.project_config if (workspace / c).exists()]
        if found and not pol.get("cli_allow_project_config"):
            raise RuntimeError(f"the workspace contains {spec.name} project configuration ({', '.join(found)}), which "
                               "could change the CLI's tool approvals; remove it or allow it explicitly with "
                               "context_policy.cli_allow_project_config")
        prompt = str(ctx.input.get("prompt", ""))
        argv = spec.build(prompt, model)
        limits = runner.default_limits()
        limits.network, limits.timeout_s = True, timeout
        yield EngineEvent("log", {"message": f"{spec.name} in the sandbox", "argv": argv[:2] + ["…"]})
        try:
            result = await RUNNER(workspace, argv, limits=limits, image=get_settings().cli_sandbox_image,
                                  secret_env={key_env: secret, **{a: secret for a in spec.key_aliases}})
        except runner.SandboxError as e:
            raise RetryableError(f"sandbox unavailable: {e}") from e
        if result.timed_out:
            raise RuntimeError(f"{spec.name} timed out after {int(timeout)} s")
        if result.exit_code == 127:
            raise RuntimeError(f"{spec.binary} is not installed in {get_settings().cli_sandbox_image}")
        if result.exit_code != 0:
            raise RuntimeError(f"{spec.name} exited with {result.exit_code}: {(result.stderr or result.stdout)[-800:]}")
        try:
            text, usage = spec.parse(result.stdout)
        except (ValueError, KeyError, IndexError) as e:
            raise RuntimeError(f"could not read {spec.name} output: {e}") from e
        if text:
            yield EngineEvent("output_delta", {"text": text})
        if usage:
            yield EngineEvent("usage", {k: v for k, v in usage.items() if k in ("input_tokens", "output_tokens")}
                              | {"provider": f"cli:{spec.key}", "cost_usd": None,
                                 "note": "CLI-reported figures; not recorded as platform cost"})


register_engine("cli", CliEngine)
