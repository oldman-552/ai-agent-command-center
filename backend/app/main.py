from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import auth
from app.bootstrap import run_bootstrap
from app.db import session_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    with session_factory()() as db:
        run_bootstrap(db)
    yield


app = FastAPI(title="AI Agent Command Center API", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)


@app.get("/api/health", tags=["system"])
def health() -> JSONResponse:
    try:
        with session_factory()() as db:
            db.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "database": "ok"})
    except Exception:  # noqa: BLE001 — must never raise
        return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
