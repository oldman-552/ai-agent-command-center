"""Browser Bridge provider adapters (BB-02, ADR-025) — EXPERIMENTAL.

An adapter is data: where the chat lives and how to find its parts. Selectors for real providers are best-effort and
UNVERIFIED (their pages change without notice and their consumer terms may prohibit automation — see ADR-005); every
real adapter stays locked until the Super Admin acknowledges that provider's terms. When the expected elements are
missing the bridge fails safely with `interface_changed` instead of guessing.

The `test` adapter drives the platform's own local test page (`/api/browser-bridge/test-page`) and is what the
automated tests use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace


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
    Adapter(key="arena", name="Arena agent (arena.ai)", url="https://arena.ai/agent",
            terms_url="https://help.arena.ai/articles/5629909088-terms-of-use",
            input="textarea", send="button[type='submit'], button[aria-label*='Send' i]",
            response="[data-message-role='assistant'], [data-role='assistant'], .prose",
            generating="button[aria-label*='Stop' i]", stop="button[aria-label*='Stop' i]",
            login="a[href*='login'], a[href*='signin'], button[aria-label*='Sign in' i]",
            notes="Guessed selectors, never checked against the live page: adjust them under Selectors. Arena's terms "
                  "prohibit automated access and its bot checks will stop the session for a person to solve.",
            terms_warning="Arena's terms explicitly prohibit accessing the service by automated means (section 5(vi)), "
                          "and inputs are shared with third-party model providers that may not keep them "
                          "confidential (section 3.4); I will not send confidential code or personal data."),
)}
# kept for code that only needs the built-in sites (tests); use catalog()/lookup() for an organization
ADAPTERS = BUILTIN

SITE_KEY_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


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
        if SITE_KEY_RE.match(k) and k not in BUILTIN and isinstance(d, dict) and d.get("url"):
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
