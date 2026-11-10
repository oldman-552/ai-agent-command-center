"""Browser Bridge provider adapters (BB-02, ADR-025) — EXPERIMENTAL.

An adapter is data: where the chat lives and how to find its parts. Selectors for real providers are best-effort and
UNVERIFIED (their pages change without notice and their consumer terms may prohibit automation — see ADR-005); every
real adapter stays locked until the Super Admin acknowledges that provider's terms. When the expected elements are
missing the bridge fails safely with `interface_changed` instead of guessing.

The `test` adapter drives the platform's own local test page (`/api/browser-bridge/test-page`) and is what the
automated tests use.
"""
from __future__ import annotations

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


ADAPTERS: dict[str, Adapter] = {a.key: a for a in (
    Adapter(key="test", name="Local test page", url="/api/browser-bridge/test-page", terms_url="",
            input="#prompt", send="#send", response=".msg.assistant", generating="#stop:not([hidden])",
            stop="#stop", login="#login-required", new_chat="#new-chat", models=("echo-1",), verified=True,
            requires_terms=False, notes="The platform's own fake chat page used for automated tests."),
    Adapter(key="claude", name="Claude (claude.ai)", url="https://claude.ai/new",
            terms_url="https://www.anthropic.com/legal/consumer-terms",
            input="div[contenteditable='true']", send="button[aria-label*='Send']",
            response="[data-is-streaming], .font-claude-message", generating="button[aria-label*='Stop']",
            stop="button[aria-label*='Stop']", login="a[href*='login'], input[type='email']",
            notes="Unverified selectors. Prefer the official API or Claude Code CLI."),
    Adapter(key="chatgpt", name="ChatGPT (chatgpt.com)", url="https://chatgpt.com/",
            terms_url="https://openai.com/policies/terms-of-use",
            input="#prompt-textarea", send="button[data-testid='send-button']",
            response="[data-message-author-role='assistant']", generating="button[data-testid='stop-button']",
            stop="button[data-testid='stop-button']", login="button[data-testid='login-button']",
            notes="Unverified selectors. Prefer the official API or Codex CLI."),
    Adapter(key="gemini", name="Gemini (gemini.google.com)", url="https://gemini.google.com/app",
            terms_url="https://policies.google.com/terms/generative-ai",
            input="rich-textarea .ql-editor", send="button[aria-label*='Send']",
            response="message-content", generating="button[aria-label*='Stop']",
            stop="button[aria-label*='Stop']", login="a[href*='accounts.google.com']",
            notes="Unverified selectors. Prefer the official API or Gemini CLI."),
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

SELECTOR_FIELDS = ("input", "send", "response", "generating", "stop", "login", "new_chat")


def effective(a: Adapter, overrides: dict[str, str] | None) -> Adapter:
    """The adapter with the organization's selector overrides; an overridden adapter is never 'verified'."""
    o = {k: v for k, v in (overrides or {}).items() if k in SELECTOR_FIELDS and isinstance(v, str) and v.strip()}
    return replace(a, **o, verified=False) if o else a


def public(a: Adapter) -> dict:
    return {"key": a.key, "name": a.name, "url": a.url, "terms_url": a.terms_url, "verified": a.verified,
            "selectors": {k: getattr(a, k) for k in SELECTOR_FIELDS},
            "requires_terms": a.requires_terms, "models": list(a.models), "notes": a.notes, "experimental": True}
