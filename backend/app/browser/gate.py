"""Browser Bridge gate (BB-03, ADR-005/025): disabled by default; every third-party provider stays locked until a human
with `browser_bridge.manage` (human-only) records an acknowledgement of that provider's terms. Both the user-facing API
and the bridge service check the gate (defence in depth). Every change is audited."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.browser.adapters import ADAPTERS
from app.models import Organization
from app.security.audit import log_activity

TERMS_STATEMENT = ("I have read {name}'s terms ({url}). I understand that automating its consumer web interface may "
                   "be prohibited, that selectors are unverified, and I accept responsibility for using it with a "
                   "dedicated account and profile.")


class GateClosed(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _cfg(org: Organization) -> dict[str, Any]:
    return dict((org.settings or {}).get("browser_bridge") or {"enabled": False, "acks": {}})


def _save(org: Organization, cfg: dict[str, Any]) -> None:
    org.settings = {**(org.settings or {}), "browser_bridge": cfg}
    flag_modified(org, "settings")


def state(db: Session, organization_id: int) -> dict[str, Any]:
    cfg = _cfg(db.get(Organization, organization_id))
    return {"enabled": bool(cfg.get("enabled")), "acks": cfg.get("acks", {})}


def statement(provider: str) -> str:
    a = ADAPTERS[provider]
    return TERMS_STATEMENT.format(name=a.name, url=a.terms_url)


def set_enabled(db: Session, organization_id: int, user_id: int, enabled: bool) -> dict[str, Any]:
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    cfg["enabled"] = bool(enabled)
    _save(org, cfg)
    log_activity(db, "browser_bridge.enabled" if enabled else "browser_bridge.disabled", category="security",
                 level="warning" if enabled else "info", organization_id=organization_id, user_id=user_id)
    return state(db, organization_id)


def acknowledge(db: Session, organization_id: int, user_id: int, provider: str, accepted: str) -> dict[str, Any]:
    if provider not in ADAPTERS or not ADAPTERS[provider].requires_terms:
        raise GateClosed("unknown_provider")
    if accepted.strip() != statement(provider):
        raise GateClosed("statement_mismatch")  # the human must accept the exact statement shown
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    acks = dict(cfg.get("acks", {}))
    acks[provider] = {"by": f"user:{user_id}", "at": datetime.now(UTC).isoformat(), "terms_url":
                      ADAPTERS[provider].terms_url, "statement": accepted.strip()}
    cfg["acks"] = acks
    _save(org, cfg)
    log_activity(db, "browser_bridge.terms_acknowledged", category="security", level="warning",
                 organization_id=organization_id, user_id=user_id, details={"provider": provider,
                                                                              "terms_url": ADAPTERS[provider].terms_url})
    return state(db, organization_id)


def revoke(db: Session, organization_id: int, user_id: int, provider: str) -> dict[str, Any]:
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    acks = dict(cfg.get("acks", {}))
    acks.pop(provider, None)
    cfg["acks"] = acks
    _save(org, cfg)
    log_activity(db, "browser_bridge.terms_revoked", category="security", organization_id=organization_id,
                 user_id=user_id, details={"provider": provider})
    return state(db, organization_id)


def check(db: Session, organization_id: int, provider: str) -> None:
    """Raise GateClosed unless the bridge is enabled and the provider is unlocked."""
    if provider not in ADAPTERS:
        raise GateClosed("unknown_provider")
    st = state(db, organization_id)
    if not st["enabled"]:
        raise GateClosed("bridge_disabled")
    if ADAPTERS[provider].requires_terms and provider not in st["acks"]:
        raise GateClosed("terms_not_acknowledged")
