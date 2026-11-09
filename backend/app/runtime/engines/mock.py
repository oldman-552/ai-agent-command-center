"""Mock execution engine — TEST ONLY, never real AI (spec rule: mocks clearly labelled).

Deterministic. Driven by `input["mock_script"]`, a list of steps:
  {"say": "text"} {"tool": "name", "args": {...}} {"sleep": 0.1} {"fail": "transient"|"fatal"}
  {"fail_until_attempt": N}  (transient failure while attempt < N — tests retries)
A checkpoint is written after every step, so a resumed execution continues from the next step.
Without a script it echoes `input["prompt"]`.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.runtime.contract import EngineContext, EngineEvent, RetryableError, register_engine

MOCK_LABEL = "[MOCK — not real AI] "


class MockEngine:
    kind = "mock"
    is_mock = True

    async def run(self, ctx: EngineContext) -> AsyncIterator[EngineEvent]:
        script = ctx.input.get("mock_script") or [{"say": f"{MOCK_LABEL}{ctx.input.get('prompt', '')}"}]
        start = int((ctx.resume_state or {}).get("next_step", 0))
        attempts = int((ctx.resume_state or {}).get("attempts", 0)) + 1
        for i in range(start, len(script)):
            step = script[i]
            if "say" in step:
                yield EngineEvent("output_delta", {"text": step["say"]})
                words = len(str(step["say"]).split())
                yield EngineEvent("usage", {"input_tokens": words, "output_tokens": words, "is_mock": True})
            elif "tool" in step:
                result = await ctx.call_tool(step["tool"], step.get("args", {}))
                yield EngineEvent("log", {"message": f"tool {step['tool']} returned", "result_keys": sorted(result)})
            elif "sleep" in step:
                await asyncio.sleep(float(step["sleep"]))
            elif step.get("fail") == "transient":
                raise RetryableError("mock transient failure")
            elif step.get("fail") == "fatal":
                raise RuntimeError("mock fatal failure")
            elif "fail_until_attempt" in step and attempts < int(step["fail_until_attempt"]):
                await ctx.checkpoint({"next_step": i, "attempts": attempts})
                raise RetryableError(f"mock transient failure (attempt {attempts})")
            await ctx.checkpoint({"next_step": i + 1, "attempts": attempts})


register_engine("mock", MockEngine)
