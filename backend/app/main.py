from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.bootstrap import run_bootstrap
from app.connectivity import providers
from app.control import agents, users
from app.db import session_factory
from app.observability import dashboard
from app.security import auth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    with session_factory()() as db:
        run_bootstrap(db)
    yield


app = FastAPI(title="AI Agent Command Center API", version="0.2.0", lifespan=lifespan)

# No CORSMiddleware on purpose: the API is same-origin only (the Next.js server proxies /api/*),
# so browsers refuse cross-origin reads. Security headers on every response:
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(users.router)
app.include_router(agents.router)
app.include_router(providers.router)


@app.get("/api/health", tags=["system"])
def health() -> JSONResponse:
    try:
        with session_factory()() as db:
            db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "database": "ok"})
    except Exception:  # noqa: BLE001 — must never raise
        return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
