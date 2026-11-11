"""Browser Bridge provider adapters (BB-02, ADR-025) — EXPERIMENTAL.

An adapter is data: where the chat lives and how to find its parts. Selectors for real providers are best-effort and
UNVERIFIED (their pages change without notice and their consumer terms may prohibit automation — see ADR-005); every
real adapter stays locked until the Super Admin acknowledges that provider's terms. When the expected elements are
missing the bridge fails safely with `interface_changed` instead of guessing.

The `test` adapter drives the platform's own local test page (`/api/browser-bridge/test-page`) and is what the
automated tests use.

Arena is not a Browser Bridge site (ADR-032): it is reached only through Arena Local Bridge, a provider of kind
`arena_local_bridge`. The former built-in `arena` site is gone and Arena's hosts are refused as custom sites.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Adapter:
    key: str
    name: str
    url: str
    terms_url: str
    input: str             # prompt box
    send: str              # send button
    response: str          # assistant messages; the last match is the current answer
    generating: str        # present while the model is generating (usually the stop button)
    stop: str              # button that cancels the generation
    login: str             # present when the user is signed out
    new_chat: str | None = None
    models: tuple[str, ...] = ()
    verified: bool = False  # selectors confirmed against the live site by a human
    requires_terms: bool = True
    notes: str = ""
    terms_warning: str = ""  # provider-specific clauses the Super Admin acknowledges explicitly
    captcha: tuple[str, ...] = field(default=(
        "iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare.com']",
        "#challenge-form", "[data-captcha]"))


BUILTIN: dict[str, Adapter] = {a.key: a for a in (
    Adapter(key="test", name="Local test page", url="/api/browser-bridge/test-page", terms_url="",
            input="#prompt", send="#send", response=".msg.assistant", generating="#stop:not([hidden])",
            stop="#stop", login="#login-required", new_chat="#new-chat", models=("echo-1",), verified=True,
            requires_terms=False, notes="The platform's own fake chat page used for automated tests."),
)}
# kept for code that only needs the built-in sites (tests); use catalog()/lookup() for an organization
ADAPTERS = BUILTIN

SITE_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")

# ADR-032 — Arena is served only by Arena Local Bridge. The key of the former built-in site stays reserved, and a site
# on one of Arena's hosts can neither be added nor used (also when an older installation still has it stored).
ARENA_RETIRED = "arena_requires_local_bridge"
RETIRED_KEYS = {"arena": ARENA_RETIRED}
ARENA_HOST_SUFFIXES = ("arena.ai", "lmarena.ai")


def is_arena_url(url: str) -> bool:
    try:
        host = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == s or host.endswith("." + s) for s in ARENA_HOST_SUFFIXES)


def retired_reason(org_settings: dict | None, key: str) -> str | None:
    """Why a site key can no longer be used, or None. Checked by the gate and again by the bridge service."""
    if key in RETIRED_KEYS:
        return RETIRED_KEYS[key]
    site = (((org_settings or {}).get("browser_bridge") or {}).get("sites") or {}).get(key)
    if isinstance(site, dict) and is_arena_url(str(site.get("url") or "")):
        return ARENA_RETIRED
    return None


def test_page_visible() -> bool:
    import os
    return os.environ.get("BROWSER_SHOW_TEST_PAGE", "").lower() in ("1", "true", "yes")


def custom_adapter(key: str, d: dict) -> Adapter:
    """A site the Super Admin added (stored in organization settings). Always unverified, always needs terms."""
    return Adapter(key=key, name=str(d.get("name") or key)[:80], url=str(d["url"]), terms_url=str(d.get("terms_url") or ""),
                   input=d.get("input") or "textarea", send=d.get("send") or "button[type='submit']",
                   response=d.get("response") or "[data-role='assistant']",
                   generating=d.get("generating") or "button[aria-label*='Stop' i]",
                   stop=d.get("stop") or "button[aria-label*='Stop' i]",
                   login=d.get("login") or "a[href*='login']", new_chat=d.get("new_chat") or None,
                   notes="Added by the Super Admin; selectors unverified.", verified=False, requires_terms=True)


def catalog(org_settings: dict | None, *, include_hidden: bool = False) -> dict[str, Adapter]:
    """Every site an organization can use: the built-in ones plus the ones it added. The local test page is listed
    only for tests and development (BROWSER_SHOW_TEST_PAGE), but always resolvable for the automated tests."""
    cfg = (org_settings or {}).get("browser_bridge") or {}
    out = {k: a for k, a in BUILTIN.items() if k != "test" or include_hidden or test_page_visible()}
    for k, d in (cfg.get("sites") or {}).items():
        if (SITE_KEY_RE.match(k) and k not in BUILTIN and k not in RETIRED_KEYS and isinstance(d, dict)
                and d.get("url") and not is_arena_url(str(d["url"]))):
            out[k] = custom_adapter(k, d)
    return out


def lookup(org_settings: dict | None, key: str) -> Adapter | None:
    return catalog(org_settings, include_hidden=True).get(key)


SELECTOR_FIELDS = ("input", "send", "response", "generating", "stop", "login", "new_chat")


def effective(a: Adapter, overrides: dict[str, str] | None) -> Adapter:
    """The adapter with the organization's selector overrides; an overridden adapter is never 'verified'."""
    o = {k: v for k, v in (overrides or {}).items() if k in SELECTOR_FIELDS and isinstance(v, str) and v.strip()}
    return replace(a, **o, verified=False) if o else a


def public(a: Adapter) -> dict:
    return {"key": a.key, "name": a.name, "url": a.url, "terms_url": a.terms_url, "verified": a.verified,
            "selectors": {k: getattr(a, k) for k in SELECTOR_FIELDS},
            "requires_terms": a.requires_terms, "models": list(a.models), "notes": a.notes, "experimental": True,
            "custom": a.key not in BUILTIN}
