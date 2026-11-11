"""Probe Arena Local Bridge from where the platform runs: `python -m app.connectivity.arena_bridge.probe`.

The health-check scripts run this **inside the API container** (`docker compose exec -T api python -m …`), because
that is the only place where the answer means something: the address that opens the bridge in a browser on the
Windows host (`http://127.0.0.1:20140`) is not the address a container has to use
(`http://host.docker.internal:20140`), and a check made on the host would pass while every worker fails.

For each configured bridge it runs the same step-by-step diagnostics as the "Test connection" button (address, DNS,
TCP, `/health`, the key probe, accounts, sessions, terms). Without a configured bridge it tries the default address
(DNS, TCP, `/health`), so the owner can see whether a bridge started on the host would be reachable before adding it.

Nothing is sent to Arena: no chat request is made. The output carries no key and no account address — counts only.

Exit code:  0 — no bridge is configured, or every enabled bridge answered and has a ready account;
           10 — an enabled bridge failed a step (the step and what to do are printed);
           11 — the probe itself could not run (database unreachable).
The codes avoid 1, 2 and 125-127 on purpose: Python and `docker compose exec` use those when the command could not
even start (the service is stopped, a traceback, a bad argument), and the scripts must tell the two apart.
The bridge is optional: Human-Assisted workers need none, so callers report a failure here as a warning.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select

from app.connectivity.arena_bridge import service
from app.connectivity.arena_bridge.adapter import KIND
from app.connectivity.arena_bridge.client import ArenaBridgeClient, BridgeConfig, BridgeError
from app.db import session_factory
from app.models import Provider
from app.security.crypto import redact

MARK = {True: "OK  ", False: "FAIL", None: "note"}
MARK_OPTIONAL = {True: "OK  ", False: "no  ", None: "note"}  # the default address: an absent bridge is not a failure
EXIT_OK, EXIT_BRIDGE_FAILED, EXIT_PROBE_FAILED = 0, 10, 11


async def probe_default(url: str = service.DEFAULT_URL) -> dict[str, Any]:
    """No bridge configured: is the default address reachable from here? DNS, TCP and `/health` only (no key)."""
    u = urlsplit(url)
    host, port = u.hostname or "", u.port or 80
    steps: list[dict[str, Any]] = []

    def step(name: str, ok: bool | None, detail: str, hint: str | None = None) -> bool:
        steps.append({"step": name, "ok": ok, "detail": detail, "hint": hint})
        return bool(ok)

    try:
        infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 5)
        step("dns", True, f"{host} resolves to {', '.join(sorted({i[4][0] for i in infos}))}")
    except (OSError, TimeoutError) as e:
        step("dns", False, f"{host} does not resolve ({type(e).__name__})",
             "Docker Desktop provides host.docker.internal; the shipped compose file maps it on Linux too")
        return {"ok": False, "steps": steps, "base_url": url}
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
        writer.close()
        await writer.wait_closed()
        step("tcp", True, f"port {port} accepts connections")
    except (OSError, TimeoutError) as e:
        step("tcp", False, f"nothing answers on {host}:{port} ({type(e).__name__})",
             "this is expected while Arena Local Bridge is not running on the host")
        return {"ok": False, "steps": steps, "base_url": url}
    client = ArenaBridgeClient(BridgeConfig.from_settings(url, None), None)
    try:
        h = await client.health()
        step("health", True, f"{h.get('service') or 'bridge'} {h.get('version')}"
             + (" - FAKE bridge (tests only)" if h.get("_fake") else ""))
    except BridgeError as e:
        step("health", False, f"{e.code}: {e.message}",
             "another program answers on this port" if e.code == "not_a_bridge" else None)
    finally:
        await client.aclose()
    return {"ok": all(s["ok"] for s in steps), "steps": steps, "base_url": url}


async def run(only: Sequence[int] | None = None) -> dict[str, Any]:
    """Diagnose every bridge (or only the given ids). The reading each diagnosis takes is stored, as the button does."""
    out: dict[str, Any] = {"in_container": service.in_container(), "bridges": [], "default": None}
    with session_factory()() as db:
        q = select(Provider.id).where(Provider.kind == KIND).order_by(Provider.id)
        ids = list(db.scalars(q if only is None else q.where(Provider.id.in_(list(only)))))
    for pid in ids:
        with session_factory()() as db:
            p = db.get(Provider, pid)
            if p is None:
                continue
            row: dict[str, Any] = {"id": p.id, "name": p.name, "base_url": p.base_url, "enabled": bool(p.enabled),
                                   "mock": bool(p.is_mock), "terms_acknowledged": service.acknowledged(p)}
            if not p.enabled:
                row.update(ok=None, ready=None, steps=[])  # switched off on purpose: not checked, not a failure
            else:
                try:
                    d = await service.diagnose(db, p)
                    db.commit()
                    # accounts are masked already; the probe prints counts only, so drop them altogether
                    steps = [{k: v for k, v in s.items() if k != "accounts"} for s in d["steps"]]
                    row.update(ok=bool(d["ok"]), ready=bool(d["ready"]), mock=bool(d["mock"]) or row["mock"], steps=steps)
                except Exception as e:  # noqa: BLE001 — one broken row must not hide the others
                    db.rollback()
                    row.update(ok=False, ready=False, steps=[{"step": "probe", "ok": False, "hint": None,
                                                               "detail": redact(f"{type(e).__name__}: {e}")[:300]}])
            out["bridges"].append(row)
    if not ids and only is None:
        out["default"] = await probe_default()
    enabled = [b for b in out["bridges"] if b["enabled"]]
    out["status"] = ("not_configured" if not ids else "disabled" if not enabled
                     else "ok" if all(b["ok"] and b["ready"] for b in enabled) else "failed")
    return out


def render(report: dict[str, Any]) -> str:
    where = "inside a container" if report["in_container"] else "on this machine (not in a container)"
    lines = [f"Arena Local Bridge - checked {where}"]

    def steps(rows: list[dict[str, Any]], mark: dict[bool | None, str] = MARK) -> None:
        for s in rows:
            lines.append(f"    {mark[s['ok']]}  {s['step']:<17} {s['detail']}")
            if s.get("hint") and s["ok"] is not True:
                lines.append(f"          -> {s['hint']}")

    for b in report["bridges"]:
        label = "FAKE bridge, tests only" if b["mock"] else "enabled" if b["enabled"] else "disabled"
        lines.append(f"  [{b['id']}] {b['name']}  {b['base_url']}  ({label})")
        if not b["enabled"]:
            lines.append("    not checked: this bridge is switched off")
            continue
        steps(b["steps"])
        if not b["ok"]:  # the connection itself: DNS, TCP, /health or the key
            failed = next((s["step"] for s in b["steps"] if s["ok"] is False and s["step"] != "terms"), "connection")
            lines.append(f"    RESULT: FAILED at '{failed}'")
        elif not b["ready"]:
            lines.append("    RESULT: reachable, but no account is ready")
        elif not b["terms_acknowledged"]:
            lines.append("    RESULT: reachable and ready; the terms are not acknowledged, so nothing is sent to Arena")
        else:
            lines.append("    RESULT: ready")
    if report["default"] is not None:
        d = report["default"]
        lines.append(f"  No bridge is configured (optional: Human-Assisted workers need none). Default address {d['base_url']}:")
        steps(d["steps"], MARK_OPTIONAL)
        lines.append("    A bridge answers there: add it under AI Workforce > Arena Local Bridge." if d["ok"]
                     else "    Nothing to fix unless you want Automatic workers.")
    lines.append(f"  Status: {report['status']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check Arena Local Bridge from where the platform runs. Sends nothing to Arena.")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--bridge", type=int, action="append", metavar="ID", help="check only this bridge (repeatable)")
    args = ap.parse_args(argv)
    try:
        report = asyncio.run(run(args.bridge))
    except Exception as e:  # noqa: BLE001
        print(f"Arena Local Bridge - the probe could not run: {redact(f'{type(e).__name__}: {e}')[:300]}", file=sys.stderr)
        return EXIT_PROBE_FAILED
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return EXIT_BRIDGE_FAILED if report["status"] == "failed" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
