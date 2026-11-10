"""Generate docs/API_REFERENCE.md and docs/DATABASE_SCHEMA.md from the code, so they always match the implementation.

    cd backend && uv run python ../scripts/gen-docs.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "doc-generation-only-" + "x" * 32)
os.environ.setdefault("APP_ENCRYPTION_KEY", "Zm9yLWRvY3Mtb25seS1ub3QtYS1yZWFsLWtleS0xMjM=")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Base  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
import app.models  # noqa: E402,F401


def api_reference() -> str:
    spec = fastapi_app.openapi()
    groups: dict[str, list[str]] = defaultdict(list)
    for path, ops in sorted(spec["paths"].items()):
        for method, op in ops.items():
            tag = (op.get("tags") or ["other"])[0]
            summary = (op.get("summary") or "").strip()
            doc = (op.get("description") or "").strip().split("\n")[0]
            groups[tag].append(f"| `{method.upper()}` | `{path}` | {summary}{(' — ' + doc) if doc and doc != summary else ''} |")
    out = ["# API reference", "", "_Generated from the FastAPI application by `scripts/gen-docs.py` — do not edit by hand._",
           "", "All endpoints are under `/api`, same-origin through the web app (the Next.js server proxies `/api/*`).",
           "Authentication: the HTTP-only session cookie set by `POST /api/auth/login`; state-changing requests must send",
           "the `X-CSRF-Token` header equal to the `acc_csrf` cookie. Permissions are enforced on every endpoint (RBAC).",
           "Machine clients use scoped keys instead: A2A keys (`/api/a2a/agents/*`) and MCP keys (`/api/mcp/`).",
           "The live OpenAPI document is served at `/docs` and `/openapi.json` while the API runs.", "",
           f"{sum(len(v) for v in groups.values())} operations.", ""]
    for tag in sorted(groups):
        out += [f"## {tag}", "", "| Method | Path | Summary |", "|---|---|---|", *groups[tag], ""]
    out += ["## Not in OpenAPI", "", "| Method | Path | Summary |", "|---|---|---|",
            "| `WS` | `/api/ws` | Live updates: session cookie + CSRF value as first message; `{topic, ids}` per change |",
            "| `POST` | `/api/mcp/` | The platform MCP server (streamable HTTP, stateless, `Authorization: Bearer acc_mcp_…`) |", ""]
    return "\n".join(out)


def database_schema() -> str:
    out = ["# Database schema", "", "_Generated from the SQLAlchemy models by `scripts/gen-docs.py` — do not edit by hand._",
           "", "PostgreSQL 16. Migrations: Alembic (`backend/alembic/versions`). LangGraph checkpoints live in the separate",
           "`langgraph` schema. Every tenant-owned table carries `organization_id`; queries are scoped by it.", ""]
    for name, table in sorted(Base.metadata.tables.items()):
        out += [f"## `{name}`", ""]
        doc = getattr(next((m.class_ for m in Base.registry.mappers if m.local_table is table), None), "__doc__", None)
        if doc and not doc.startswith(("Base", "Declarative")):
            out += [" ".join(doc.split())[:400], ""]
        out += ["| Column | Type | Null | Notes |", "|---|---|---|---|"]
        for c in table.columns:
            notes = []
            if c.primary_key:
                notes.append("PK")
            for fk in c.foreign_keys:
                notes.append(f"FK → `{fk.target_fullname}`" + (f" ({fk.ondelete})" if fk.ondelete else ""))
            if c.unique:
                notes.append("unique")
            if c.index:
                notes.append("indexed")
            try:
                typ = str(c.type)
            except Exception:  # noqa: BLE001
                typ = type(c.type).__name__
            out.append(f"| `{c.name}` | {typ} | {'yes' if c.nullable else 'no'} | {', '.join(notes)} |")
        cons = [str(x.name) for x in table.constraints if x.name and not str(x.name).startswith("pk_")]
        if cons:
            out += ["", "Constraints: " + ", ".join(f"`{x}`" for x in sorted(cons))]
        out.append("")
    return "\n".join(out)


(ROOT / "docs" / "API_REFERENCE.md").write_text(api_reference())
(ROOT / "docs" / "DATABASE_SCHEMA.md").write_text(database_schema())
print("wrote docs/API_REFERENCE.md and docs/DATABASE_SCHEMA.md")
