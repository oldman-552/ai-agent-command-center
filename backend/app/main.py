from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.bootstrap import run_bootstrap
from app.browser import api as browser_api
from app.browser import engine as _browser_engine  # noqa: F401  (registers the experimental browser engine)
from app.connectivity import providers
from app.connectivity.a2a import engine as _a2a_engine  # noqa: F401  (registers the a2a engine + adapter)
from app.connectivity.a2a import keys as a2a_keys
from app.connectivity.a2a import server as a2a_server
from app.control import agents, users
from app.control import teams as teams_api
from app.db import session_factory
from app.devplane import api as devplane_api
from app.devplane import tools as _devplane_tools  # noqa: F401  (repo.read/files.write/git.*)
from app.evaluation import api as evaluation_api
from app.governance import api as approvals_api
from app.governance import audit_api, budgets_api
from app.integrations import api as integrations_api
from app.integrations import mcp_client as _mcp_client  # noqa: F401  (registers mcp:* tools)
from app.integrations.mcp_server import endpoint as mcp_endpoint
from app.memory import api as memory_api
from app.memory import tools as _memory_tools  # noqa: F401  (memory.write)
from app.observability import analytics as analytics_api
from app.observability import dashboard
from app.orchestration import api as orchestration_api
from app.runtime import api as runtime_api
from app.runtime.engines import mock as _mock_engine  # noqa: F401  (registers the mock engine)
from app.sandbox import tools as _sandbox_tools  # noqa: F401  (registers shell.test / shell.build)
from app.security import auth
from app.supervisor import api as supervisor_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    with session_factory()() as db:
        run_bootstrap(db)
    async with mcp_endpoint.running():  # the platform's MCP server (MCP-02)
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
app.include_router(runtime_api.router)
app.include_router(a2a_keys.router)
app.include_router(a2a_server.router)
app.include_router(orchestration_api.router)
app.include_router(approvals_api.router)
app.include_router(audit_api.router)
app.include_router(evaluation_api.router)
app.include_router(devplane_api.router)
app.include_router(supervisor_api.router)
app.include_router(memory_api.router)
app.include_router(teams_api.router)
app.include_router(budgets_api.router)
app.include_router(budgets_api.routing_router)
app.include_router(analytics_api.router)
app.include_router(browser_api.router)
app.include_router(integrations_api.router)
app.mount("/api/mcp", mcp_endpoint)
app.include_router(memory_api.context_router)


@app.get("/api/health", tags=["system"])
def health() -> JSONResponse:
    try:
        with session_factory()() as db:
            db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "database": "ok"})
    except Exception:  # noqa: BLE001 — must never raise
        return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
