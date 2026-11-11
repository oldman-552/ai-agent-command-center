"""Browser Bridge gate (BB-03, ADR-005/025): disabled by default; every third-party provider stays locked until a human
with `browser_bridge.manage` (human-only) records an acknowledgement of that provider's terms. Both the user-facing API
and the bridge service check the gate (defence in depth). Every change is audited."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.browser.adapters import (
    ARENA_RETIRED,
    BUILTIN,
    RETIRED_KEYS,
    SITE_KEY_RE,
    Adapter,
    is_arena_url,
    lookup,
    retired_reason,
)
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
    return {"enabled": bool(cfg.get("enabled")), "acks": cfg.get("acks", {}), "selectors": cfg.get("selectors", {})}


def adapter(db: Session, organization_id: int, provider: str) -> Adapter | None:
    org = db.get(Organization, organization_id)
    return lookup(org.settings if org else None, provider)


def overrides(db: Session, organization_id: int, provider: str) -> dict[str, str]:
    return dict(_cfg(db.get(Organization, organization_id)).get("selectors", {}).get(provider) or {})


def set_selectors(db: Session, organization_id: int, user_id: int, provider: str, sel: dict[str, str]) -> dict:
    """Super Admin only (API). Empty values fall back to the built-in selector; audited with the new values."""
    from app.browser.adapters import SELECTOR_FIELDS
    if adapter(db, organization_id, provider) is None:
        raise GateClosed("unknown_provider")
    clean: dict[str, str] = {}
    for k, v in sel.items():
        if k not in SELECTOR_FIELDS:
            raise GateClosed(f"unknown_selector:{k}")
        if not isinstance(v, str) or len(v) > 500 or "\n" in v:
            raise GateClosed(f"invalid_selector:{k}")
        if v.strip():
            clean[k] = v.strip()
    org = db.get(Organization, organization_id)
    cfg = _cfg(org)
    all_sel = dict(cfg.get("selectors", {}))
    if clean:
        all_sel[provider] = clean
    else:
        all_sel.pop(provider, None)
    cfg["selectors"] = all_sel
    _save(org, cfg)
    log_activity(db, "browser_bridge.selectors_changed", category="security", level="warning",
                 organization_id=organization_id, actor=f"user:{user_id}",
                 details={"provider": provider, "selectors": clean})
    return state(db, organization_id)


def statement(provider: str | Adapter) -> str:
    a = provider if isinstance(provider, Adapter) else BUILTIN[provider]
    return TERMS_STATEMENT.format(name=a.name, url=a.terms_url) + (f" {a.terms_warning}" if a.terms_warning else "")


def set_enabled(db: Session, organization_id: int, user_id: int, enabled: bool) -> dict[str, Any]:
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    cfg["enabled"] = bool(enabled)
    _save(org, cfg)
    log_activity(db, "browser_bridge.enabled" if enabled else "browser_bridge.disabled", category="security",
                 level="warning" if enabled else "info", organization_id=organization_id, user_id=user_id)
    return state(db, organization_id)


def retired(db: Session, organization_id: int, provider: str) -> str | None:
    """ADR-032: the reason a site can no longer be driven by the Browser Bridge (Arena), or None."""
    org = db.get(Organization, organization_id)
    return retired_reason(org.settings if org else None, provider)


def acknowledge(db: Session, organization_id: int, user_id: int, provider: str, accepted: str) -> dict[str, Any]:
    if (why := retired(db, organization_id, provider)) is not None:
        raise GateClosed(why)
    a = adapter(db, organization_id, provider)
    if a is None or not a.requires_terms:
        raise GateClosed("unknown_provider")
    if accepted.strip() != statement(a):
        raise GateClosed("statement_mismatch")  # the human must accept the exact statement shown
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    acks = dict(cfg.get("acks", {}))
    acks[provider] = {"by": f"user:{user_id}", "at": datetime.now(UTC).isoformat(), "terms_url": a.terms_url,
                      "statement": accepted.strip()}
    cfg["acks"] = acks
    _save(org, cfg)
    log_activity(db, "browser_bridge.terms_acknowledged", category="security", level="warning",
                 organization_id=organization_id, user_id=user_id, details={"provider": provider,
                                                                              "terms_url": a.terms_url})
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
    if (why := retired(db, organization_id, provider)) is not None:
        raise GateClosed(why)
    a = adapter(db, organization_id, provider)
    if a is None:
        raise GateClosed("unknown_provider")
    st = state(db, organization_id)
    if not st["enabled"]:
        raise GateClosed("bridge_disabled")
    if a.requires_terms and provider not in st["acks"]:
        raise GateClosed("terms_not_acknowledged")


# ---- sites the Super Admin adds, and browser profiles (one per person, agent or account) ----
def _clean_url(url: Any) -> str:
    from urllib.parse import urlsplit
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise GateClosed("invalid_url")
    u = urlsplit(url.strip())
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        raise GateClosed("invalid_url")  # https only: the bridge signs in to these sites
    return url.strip()


def add_site(db: Session, organization_id: int, user_id: int, key: str, d: dict[str, Any]) -> dict[str, Any]:
    from app.browser.adapters import SELECTOR_FIELDS
    if not SITE_KEY_RE.match(key or "") or key in BUILTIN or key in RETIRED_KEYS:
        raise GateClosed("invalid_site_key")
    if is_arena_url(str(d.get("url") or "")):
        raise GateClosed(ARENA_RETIRED)  # ADR-032: Arena only through Arena Local Bridge
    name = str(d.get("name") or "").strip()
    if not name or len(name) > 80:
        raise GateClosed("invalid_site_name")
    site: dict[str, Any] = {"name": name, "url": _clean_url(d.get("url")),
                            "terms_url": _clean_url(d.get("terms_url")) if d.get("terms_url") else "",
                            "added_by": f"user:{user_id}", "added_at": datetime.now(UTC).isoformat()}
    for k in SELECTOR_FIELDS:
        v = d.get(k)
        if v is not None:
            if not isinstance(v, str) or len(v) > 500 or "\n" in v:
                raise GateClosed(f"invalid_selector:{k}")
            if v.strip():
                site[k] = v.strip()
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    sites = dict(cfg.get("sites", {}))
    if key in sites:
        raise GateClosed("site_exists")
    if len(sites) >= 50:
        raise GateClosed("too_many_sites")
    sites[key] = site
    cfg["sites"] = sites
    _save(org, cfg)
    log_activity(db, "browser_bridge.site_added", category="security", level="warning",
                 organization_id=organization_id, user_id=user_id, details={"site": key, "url": site["url"]})
    return state(db, organization_id)


def remove_site(db: Session, organization_id: int, user_id: int, key: str) -> dict[str, Any]:
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    sites = dict(cfg.get("sites", {}))
    if key not in sites:
        raise GateClosed("unknown_provider")
    sites.pop(key)
    cfg["sites"] = sites
    for section in ("acks", "selectors"):  # a removed site leaves nothing unlocked behind
        cfg[section] = {k: v for k, v in (cfg.get(section) or {}).items() if k != key}
    _save(org, cfg)
    log_activity(db, "browser_bridge.site_removed", category="security", organization_id=organization_id,
                 user_id=user_id, details={"site": key})
    return state(db, organization_id)


def profiles(db: Session, organization_id: int) -> dict[str, dict[str, Any]]:
    return dict(_cfg(db.get(Organization, organization_id)).get("profiles") or {})


def add_profile(db: Session, organization_id: int, user_id: int, name: str, label: str,
                owner: str | None) -> dict[str, Any]:
    from app.browser.bridge import PROFILE_RE
    if not PROFILE_RE.match(name or ""):
        raise GateClosed("invalid_profile_name")
    if owner is not None and not owner.startswith(("user:", "agent:")):
        raise GateClosed("invalid_owner")
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    prof = dict(cfg.get("profiles", {}))
    if name in prof:
        raise GateClosed("profile_exists")
    prof[name] = {"label": (label or name).strip()[:80], "owner": owner, "created_by": f"user:{user_id}",
                  "created_at": datetime.now(UTC).isoformat()}
    cfg["profiles"] = prof
    _save(org, cfg)
    log_activity(db, "browser_bridge.profile_created", category="browser", organization_id=organization_id,
                 user_id=user_id, details={"profile": name, "owner": owner})
    return prof[name]


def ensure_profile(db: Session, organization_id: int, user_id: int, name: str, label: str, owner: str) -> None:
    """The automatic profile of a person or an agent, created the first time it is needed (by `user_id`)."""
    if name not in profiles(db, organization_id):
        add_profile(db, organization_id, user_id, name, label, owner)


def remove_profile(db: Session, organization_id: int, user_id: int, name: str) -> None:
    org = db.get(Organization, organization_id, with_for_update=True, populate_existing=True)
    cfg = _cfg(org)
    prof = dict(cfg.get("profiles", {}))
    if name not in prof:
        raise GateClosed("unknown_profile")
    prof.pop(name)
    cfg["profiles"] = prof
    _save(org, cfg)
    log_activity(db, "browser_bridge.profile_deleted", category="browser", organization_id=organization_id,
                 user_id=user_id, details={"profile": name})
