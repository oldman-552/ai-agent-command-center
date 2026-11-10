"""`shell.test` / `shell.build` runtime tools: policy check → Docker sandbox → audited result (SEC-04/05).

The runtime has already verified the agent holds the tool permission. Here the *command* is checked against that
tool's allow-list, and it runs in the execution's project workspace only. Results are data for the agent; nothing in
them is executed by the platform.
"""
from __future__ import annotations

from typing import Any

from app.db import session_factory
from app.models import Execution
from app.runtime.service import register_tool
from app.sandbox import policy, runner
from app.security.audit import log_activity


def _audit(ex: Execution, action: str, level: str, details: dict[str, Any]) -> None:
    with session_factory()() as db:
        log_activity(db, action, category="sandbox", level=level, organization_id=ex.organization_id,
                     actor=f"agent:{ex.agent_id}", agent_id=ex.agent_id, project_id=ex.project_id,
                     task_id=ex.task_id, execution_id=ex.id, details=details)
        db.commit()


def _tool(name: str):
    async def run_command(ex: Execution, args: dict[str, Any]) -> dict[str, Any]:
        argv = args.get("argv")
        decision = policy.check(name, argv)
        if not decision.allowed:
            _audit(ex, "sandbox.command_denied", "warning", {"tool": name, "argv": argv, "reason": decision.reason})
            return {"error": "command_denied", "reason": decision.reason}
        if ex.project_id is None:
            return {"error": "no_workspace", "reason": "the execution is not attached to a project"}
        try:
            ws = runner.resolve_workspace(runner.ensure_workspace(ex.organization_id, ex.project_id),
                                          ex.organization_id, ex.project_id)
            result = await runner.run(ws, argv)
        except runner.SandboxError as e:
            _audit(ex, "sandbox.unavailable", "error", {"tool": name, "argv": argv, "error": str(e)})
            return {"error": "sandbox_unavailable", "reason": str(e)}
        _audit(ex, "sandbox.command_ran", "info", {"tool": name, "argv": argv, "exit_code": result.exit_code,
                                                  "timed_out": result.timed_out,
                                                  "duration_s": round(result.duration_s, 3)})
        return result.as_dict()
    return run_command


for _name in policy.ALLOWED:
    register_tool(_name, _tool(_name))
