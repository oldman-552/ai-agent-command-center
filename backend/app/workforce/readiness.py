"""Team readiness (ADR-037): one place that says, before a goal is sent, whether every member of the AI Workforce can
work through its own browser window — and what is missing, with the fix for each.

Read-only: nothing here types into a window, sends anything to a site or changes a setting. The fixes are the
existing actions (enable the Browser Bridge, acknowledge a site's terms, open a member's window), each still behind
its own permission and gate.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.browser import adapters as browser_adapters
from app.browser import client as browser_client
from app.browser import gate
from app.browser.bridge import BridgeError
from app.connectivity.arena_bridge import service as bridge
from app.models import Agent, Organization, Provider, Workforce
from app.workforce import service

SITE = "arena"
WINDOW_OK = {"READY", "COMPLETED", "SENDING", "GENERATING"}


def _connection(db: Session, agent: Agent) -> tuple[str, Any]:
    """window | old_bridge | provider | none — and the window session, if any."""
    if agent.execution_engine == "browser":
        return "window", service.browser_session_of(db, agent)
    p = db.get(Provider, agent.provider_id) if agent.provider_id else None
    if p is None or not agent.model:
        return "none", None
    return ("old_bridge" if bridge.is_bridge(p) else "provider"), None


def _member(db: Session, kind: str, label: str, agent: Agent, worker_id: int | None, mode: str | None) -> dict:
    conn, s = _connection(db, agent)
    problem = None
    if mode == "DISABLED":
        problem = None
    elif conn == "window":
        if s is None:
            problem = "no_window"
        elif s.state == "AUTH_REQUIRED":
            problem = "sign_in"
        elif s.state not in WINDOW_OK:
            problem = "window_closed"
    elif conn == "old_bridge":
        problem = "old_connection"
    elif conn == "none":
        problem = "no_connection"
    return {"kind": kind, "worker_id": worker_id, "label": label, "name": agent.name, "mode": mode,
            "connection": conn, "window_state": s.state if s else None, "window_error": s.error if s else None,
            "problem": problem}


async def readiness(db: Session, wf: Workforce) -> dict[str, Any]:
    org_id = wf.organization_id
    st = gate.state(db, org_id)
    try:
        await browser_client.call("GET", "/health")
        reachable, bridge_error = True, None
    except BridgeError as e:
        reachable, bridge_error = False, e.code
    a = browser_adapters.catalog(db.get(Organization, org_id).settings).get(SITE)
    members = []
    if wf.manager_agent_id and (m := db.get(Agent, wf.manager_agent_id)) is not None:
        members.append(_member(db, "manager", "Manager", m, None, None))
    for w in service.workers(db, wf.id):
        members.append(_member(db, "worker", f"W{w.slot}", db.get(Agent, w.agent_id), w.id, w.mode))
    terms = {"site": SITE, "acknowledged": SITE in st["acks"],
             "statement": gate.statement(a) if a is not None and a.requires_terms else None}
    checks = {"bridge_enabled": bool(st["enabled"]), "bridge_running": reachable, "terms": terms["acknowledged"]}
    ready = all(checks.values()) and not any(x["problem"] for x in members)
    return {"ready": ready, "checks": checks, "bridge_error": bridge_error, "terms": terms, "members": members,
            "site": SITE}
