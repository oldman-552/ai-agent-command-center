"""`browser` execution engine (EXPERIMENTAL, ADR-025): runs an agent's turn through a Browser Bridge session.

The agent's `context_policy.browser_session_id` names a session of the same organization. The gate is re-checked by
the bridge on every prompt. Output is the visible answer text only; it is labelled as coming from a browser session.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.browser import client
from app.browser.bridge import BridgeError
from app.db import session_factory
from app.models import Agent, BrowserSession, Execution
from app.runtime.contract import EngineContext, EngineEvent, RetryableError, register_engine


class BrowserEngine:
    kind = "browser"
    is_mock = False

    async def run(self, ctx: EngineContext) -> AsyncIterator[EngineEvent]:
        sid = (ctx.agent.get("context_policy") or {}).get("browser_session_id")
        with session_factory()() as db:
            ex = db.get(Execution, ctx.execution_id)
            agent = db.get(Agent, ex.agent_id)
            s = db.get(BrowserSession, int(sid)) if sid else None
            if s is None or s.organization_id != agent.organization_id:
                raise RuntimeError("the agent has no Browser Bridge session (context_policy.browser_session_id)")
            ex.provider, ex.model = f"browser:{s.provider_key}", s.model or "web"
            db.commit()
        prompt = str(ctx.input.get("prompt", ""))
        try:
            snap = await client.call("POST", f"/sessions/{sid}/prompt", {"prompt": prompt})
        except BridgeError as e:
            if e.code in ("bridge_unreachable", "too_many_sessions"):
                raise RetryableError(e.code) from e
            raise RuntimeError(f"browser bridge refused: {e.code}") from e
        sent = 0
        yield EngineEvent("log", {"message": f"browser session {sid} ({snap['provider']}) — experimental"})
        while snap["state"] in ("SENDING", "GENERATING"):
            if await ctx.is_cancelled():
                await client.call("POST", f"/sessions/{sid}/cancel")
                return
            part = snap.get("partial") or ""
            if len(part) > sent:
                yield EngineEvent("output_delta", {"text": part[sent:]})
                sent = len(part)
            await asyncio.sleep(0.5)
            snap = await client.call("GET", f"/sessions/{sid}")
        if snap["state"] != "COMPLETED":
            raise RuntimeError(f"browser session ended in {snap['state']}: {snap.get('error')}")
        with session_factory()() as db:
            hist = db.get(BrowserSession, int(sid)).history or []
        final = next((h["text"] for h in reversed(hist) if h.get("role") == "assistant"), "")
        if len(final) > sent:
            yield EngineEvent("output_delta", {"text": final[sent:]})


register_engine("browser", BrowserEngine)
