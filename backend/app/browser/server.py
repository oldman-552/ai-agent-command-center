"""The Browser Bridge as a standalone service (`python -m app.browser.server`, port 8100) — ADR-025.

It is the only process that owns browsers. It listens on localhost only and accepts calls from the platform API and
workers carrying an internal token derived from the platform secret; it re-checks the gate on every start/send.
"""
from __future__ import annotations

import hashlib
import hmac
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.browser.bridge import Bridge, BridgeError, get_bridge
from app.config import get_settings


def internal_token() -> str:
    return hmac.new(get_settings().jwt_secret.encode(), b"browser-bridge-internal", hashlib.sha256).hexdigest()


def _auth(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {internal_token()}"):
        raise HTTPException(401, "unauthorized")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Bridge.mark_orphans_disconnected()
    yield
    await get_bridge().shutdown()


app = FastAPI(title="ACC Browser Bridge (experimental)", lifespan=lifespan, dependencies=[Depends(_auth)],
              docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(BridgeError)
async def _bridge_error(_request, e: BridgeError):
    return JSONResponse({"detail": e.code}, status_code=e.status)


class StartIn(BaseModel):
    options: dict[str, Any] = Field(default_factory=dict)


class PromptIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=100_000)


@app.get("/health")
async def health() -> dict:
    return get_bridge().health()


@app.post("/sessions/{sid}/start")
async def start(sid: int, body: StartIn) -> dict:
    return await get_bridge().start(sid, body.options)


@app.post("/sessions/{sid}/recheck")
async def recheck(sid: int) -> dict:
    return await get_bridge().recheck(sid)


@app.post("/sessions/{sid}/stop")
async def stop(sid: int) -> dict:
    return await get_bridge().stop(sid)


@app.post("/sessions/{sid}/conversation")
async def conversation(sid: int) -> dict:
    return await get_bridge().new_conversation(sid)


@app.post("/sessions/{sid}/prompt")
async def prompt(sid: int, body: PromptIn) -> dict:
    return await get_bridge().send(sid, body.prompt)


@app.post("/sessions/{sid}/cancel")
async def cancel(sid: int) -> dict:
    return await get_bridge().cancel(sid)


@app.get("/sessions/{sid}")
async def status(sid: int) -> dict:
    return get_bridge().snapshot(sid)


if __name__ == "__main__":
    import uvicorn

    from app.observability.logs import setup_logging
    setup_logging("browser-bridge")
    uvicorn.run(app, host="127.0.0.1", port=int(get_settings().browser_bridge_url.rsplit(":", 1)[-1].split("/")[0]))
