"""Browser Bridge core (BB-02, BB-04, ADR-025) — EXPERIMENTAL, disabled by default.

One Playwright persistent context per session, each in its own dedicated profile directory under
`BROWSER_PROFILES_DIR/<org>/<provider>/<profile>` — never a personal browser profile. The bridge only ever reads the
*visible text* of the answer; it has no code path that reads, returns or logs cookies, storage or credentials. When a
CAPTCHA or a sign-in page appears it stops in AUTH_REQUIRED and waits for a human; it never tries to solve or bypass
either. Missing page elements mean the provider changed its interface: the session FAILS with `interface_changed`.

States (spec Phase 11): DISCONNECTED → STARTING → AUTH_REQUIRED | READY → SENDING → GENERATING → COMPLETED → SENDING …
plus FAILED and STOPPED. The state lives in PostgreSQL; the live browser objects live in this process only.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.browser import gate
from app.browser.adapters import ADAPTERS, Adapter, effective
from app.config import get_settings
from app.db import session_factory
from app.models import BrowserSession
from app.models import BrowserSessionState as S
from app.security.audit import log_activity
from app.security.crypto import redact

log = logging.getLogger("browser.bridge")

TRANSITIONS: dict[str, set[str]] = {
    S.DISCONNECTED.value: {S.STARTING.value, S.STOPPED.value},
    S.STARTING.value: {S.AUTH_REQUIRED.value, S.READY.value, S.FAILED.value, S.STOPPED.value},
    S.AUTH_REQUIRED.value: {S.READY.value, S.AUTH_REQUIRED.value, S.FAILED.value, S.STOPPED.value},
    S.READY.value: {S.SENDING.value, S.FAILED.value, S.STOPPED.value, S.DISCONNECTED.value, S.READY.value},
    S.SENDING.value: {S.GENERATING.value, S.FAILED.value, S.STOPPED.value, S.AUTH_REQUIRED.value},
    S.GENERATING.value: {S.COMPLETED.value, S.READY.value, S.FAILED.value, S.STOPPED.value},
    S.COMPLETED.value: {S.SENDING.value, S.READY.value, S.FAILED.value, S.STOPPED.value, S.DISCONNECTED.value},
    S.FAILED.value: {S.STARTING.value, S.STOPPED.value},
    S.STOPPED.value: {S.STARTING.value},
}
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SENDABLE = {S.READY.value, S.COMPLETED.value}
ACTIVE = {S.STARTING.value, S.AUTH_REQUIRED.value, S.READY.value, S.SENDING.value, S.GENERATING.value,
          S.COMPLETED.value}
MAX_PROMPT = 100_000


class BridgeError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def transition(db: Session, s: BrowserSession, to: str, error: str | None = None) -> None:
    if to not in TRANSITIONS.get(s.state, set()):
        raise BridgeError(f"invalid_transition:{s.state}->{to}")
    s.state, s.error, s.last_activity_at = to, error, datetime.now(UTC)
    if to in (S.FAILED.value, S.AUTH_REQUIRED.value, S.STOPPED.value):
        log_activity(db, f"browser_bridge.{to.lower()}", category="browser", organization_id=s.organization_id,
                     agent_id=s.agent_id, level="warning" if to == S.FAILED.value else "info",
                     details={"session_id": s.id, "provider": s.provider_key, "error": error})


def profile_dir(organization_id: int, provider: str, profile: str) -> Path:
    """A dedicated, platform-owned profile directory. Anything else is refused."""
    if not PROFILE_RE.match(profile or ""):
        raise BridgeError("invalid_profile_name", 422)
    base = Path(get_settings().browser_profiles_dir).resolve()
    path = (base / str(int(organization_id)) / provider / profile).resolve()
    if base not in path.parents:
        raise BridgeError("invalid_profile_name", 422)
    return path


@dataclass
class Runtime:
    context: Any
    page: Any
    adapter: Adapter
    partial: str = ""
    monitor: asyncio.Task | None = None
    cancelled: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Bridge:
    """Owns the live browsers of this process. Use one instance per process (see `get_bridge`)."""

    def __init__(self, base_url: str | None = None, poll: float = 0.25):
        self.base_url = (base_url or "http://127.0.0.1:8000").rstrip("/")
        self.poll = poll
        self._pw = None
        self._rt: dict[int, Runtime] = {}
        self._start_lock = asyncio.Lock()

    # ---------------- helpers ----------------
    def _db(self) -> Session:
        return session_factory()()

    async def _playwright(self):
        if self._pw is None:
            from playwright.async_api import async_playwright
            self._pw = await async_playwright().start()
        return self._pw

    def _url(self, a: Adapter, options: dict[str, Any]) -> str:
        if a.key != "test":
            return a.url
        mode = str(options.get("test_mode", ""))
        q = f"?mode={mode}" if re.fullmatch(r"[a-z_]{1,20}", mode) else ""
        return f"{self.base_url}{a.url}{q}"

    @staticmethod
    async def _visible(page, selector: str) -> bool:
        try:
            loc = page.locator(selector).first
            return await loc.count() > 0 and await loc.is_visible()
        except Exception:  # noqa: BLE001 — a selector error is "not there"
            return False

    async def _detect(self, rt: Runtime) -> tuple[str, str | None]:
        """READY, or AUTH_REQUIRED (captcha/login), or FAILED (interface changed). Never interacts with the page."""
        page, a = rt.page, rt.adapter
        for sel in a.captcha:
            if await page.locator(sel).count() > 0:
                return S.AUTH_REQUIRED.value, "captcha_detected"
        if await self._visible(page, a.login):
            return S.AUTH_REQUIRED.value, "login_required"
        with contextlib.suppress(Exception):
            await page.locator(a.input).first.wait_for(state="visible", timeout=5_000)
        if await self._visible(page, a.input):
            return S.READY.value, None
        return S.FAILED.value, "interface_changed"

    # ---------------- lifecycle ----------------
    async def start(self, session_id: int, options: dict[str, Any] | None = None) -> dict[str, Any]:
        settings = get_settings()
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            if s is None:
                raise BridgeError("not_found", 404)
            try:
                gate.check(db, s.organization_id, s.provider_key)
            except gate.GateClosed as e:
                raise BridgeError(e.code, 403) from e
            if session_id in self._rt:
                db.rollback()
                return self.snapshot(session_id)
            if len(self._rt) >= settings.browser_max_sessions:
                raise BridgeError("too_many_sessions", 429)
            a = effective(ADAPTERS[s.provider_key], gate.overrides(db, s.organization_id, s.provider_key))
            pdir = profile_dir(s.organization_id, s.provider_key, s.profile_name)
            transition(db, s, S.STARTING.value)
            db.commit()
        try:
            async with self._start_lock:
                pw = await self._playwright()
                pdir.mkdir(parents=True, exist_ok=True, mode=0o700)
                ctx = await pw.chromium.launch_persistent_context(
                    str(pdir), headless=settings.browser_headless, accept_downloads=False,
                    args=["--no-first-run", "--no-default-browser-check", "--disable-sync"])
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            rt = Runtime(context=ctx, page=page, adapter=a)
            self._rt[session_id] = rt
            await page.goto(self._url(a, options or {}), wait_until="domcontentloaded", timeout=30_000)
            to, err = await self._detect(rt)
        except Exception as e:  # noqa: BLE001 — any launch/navigation failure fails the session safely
            to, err = S.FAILED.value, f"start_failed: {type(e).__name__}: {str(e)[:300]}"
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            transition(db, s, to, err)
            db.commit()
        if to == S.FAILED.value:
            await self._close(session_id)
        return self.snapshot(session_id, state=to, error=err)

    async def recheck(self, session_id: int) -> dict[str, Any]:
        """After a human signed in / solved a challenge in the bridge window: re-detect, nothing else."""
        rt = self._rt.get(session_id)
        if rt is None:
            raise BridgeError("not_running")
        to, err = await self._detect(rt)
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            if s.state != S.AUTH_REQUIRED.value:
                raise BridgeError("not_waiting_for_auth")
            transition(db, s, to, err)
            db.commit()
        return self.snapshot(session_id, state=to, error=err)

    async def stop(self, session_id: int) -> dict[str, Any]:
        await self._close(session_id)
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            if s is None:
                raise BridgeError("not_found", 404)
            if s.state != S.STOPPED.value:
                transition(db, s, S.STOPPED.value)
            db.commit()
        return self.snapshot(session_id, state=S.STOPPED.value)

    async def _close(self, session_id: int) -> None:
        rt = self._rt.pop(session_id, None)
        if rt is None:
            return
        if rt.monitor and not rt.monitor.done():
            rt.monitor.cancel()
        with contextlib.suppress(Exception):
            await rt.context.close()

    async def shutdown(self) -> None:
        for sid in list(self._rt):
            await self._close(sid)
            with self._db() as db:
                s = db.get(BrowserSession, sid)
                if s is not None and s.state in ACTIVE:
                    s.state, s.error = S.DISCONNECTED.value, "bridge shut down"
                db.commit()
        if self._pw is not None:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None

    @staticmethod
    def mark_orphans_disconnected() -> int:
        """At bridge start no browser is alive: sessions left active by a previous process are DISCONNECTED."""
        with session_factory()() as db:
            rows = db.scalars(select(BrowserSession).where(BrowserSession.state.in_(ACTIVE))).all()
            for s in rows:
                s.state, s.error = S.DISCONNECTED.value, "bridge restarted"
            db.commit()
            return len(rows)

    # ---------------- conversation ----------------
    async def new_conversation(self, session_id: int) -> dict[str, Any]:
        rt = self._live(session_id)
        a = rt.adapter
        if a.new_chat and await self._visible(rt.page, a.new_chat):
            await rt.page.locator(a.new_chat).first.click()
        else:
            await rt.page.goto(self._url(a, {}) if a.key != "test" else rt.page.url, wait_until="domcontentloaded")
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            s.conversation_ref, s.history = None, []
            db.commit()
        return self.snapshot(session_id)

    def _live(self, session_id: int) -> Runtime:
        rt = self._rt.get(session_id)
        if rt is None:
            raise BridgeError("not_running")
        return rt

    async def send(self, session_id: int, prompt: str) -> dict[str, Any]:
        if not prompt or len(prompt) > MAX_PROMPT:
            raise BridgeError("invalid_prompt", 422)
        rt = self._live(session_id)
        async with rt.lock:
            with self._db() as db:
                s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
                try:
                    gate.check(db, s.organization_id, s.provider_key)  # re-checked on every prompt
                except gate.GateClosed as e:
                    raise BridgeError(e.code, 403) from e
                if s.state not in SENDABLE:
                    raise BridgeError(f"not_ready:{s.state}")
                transition(db, s, S.SENDING.value)
                s.history = [*s.history, {"role": "user", "text": redact(prompt), "at": datetime.now(UTC).isoformat()}]
                db.commit()
            page, a = rt.page, rt.adapter
            try:
                before = await page.locator(a.response).count()
                box = page.locator(a.input).first
                await box.wait_for(state="visible", timeout=10_000)
                await box.fill(prompt)
                if await self._visible(page, a.send):
                    await page.locator(a.send).first.click()
                else:
                    await box.press("Enter")
                rt.partial, rt.cancelled = "", False
                deadline = asyncio.get_running_loop().time() + 15
                while asyncio.get_running_loop().time() < deadline:
                    if await page.locator(a.response).count() > before or await self._visible(page, a.generating):
                        break
                    for sel in a.captcha:
                        if await page.locator(sel).count() > 0:
                            raise BridgeError("captcha_detected")
                    await asyncio.sleep(self.poll)
                else:
                    raise BridgeError("interface_changed")
            except BridgeError as e:
                self._finish(session_id, S.AUTH_REQUIRED.value if e.code == "captcha_detected" else S.FAILED.value,
                             e.code)
                return self.snapshot(session_id)
            except Exception as e:  # noqa: BLE001
                self._finish(session_id, S.FAILED.value, f"send_failed: {type(e).__name__}")
                return self.snapshot(session_id)
            with self._db() as db:
                s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
                transition(db, s, S.GENERATING.value)
                db.commit()
            rt.monitor = asyncio.create_task(self._monitor(session_id, before))
        return self.snapshot(session_id)

    async def _monitor(self, session_id: int, before: int) -> None:
        rt = self._rt[session_id]
        page, a = rt.page, rt.adapter
        loop = asyncio.get_running_loop()
        deadline = loop.time() + get_settings().browser_generation_timeout_seconds
        stable, last = 0, None
        try:
            while True:
                n = await page.locator(a.response).count()
                if n > before:
                    rt.partial = await page.locator(a.response).nth(n - 1).inner_text()
                generating = await self._visible(page, a.generating)
                stable = stable + 1 if (rt.partial == last and not generating) else 0
                last = rt.partial
                if rt.partial and stable >= 2:
                    self._finish(session_id, S.COMPLETED.value, None, answer=rt.partial)
                    return
                if loop.time() > deadline:
                    with contextlib.suppress(Exception):
                        await page.locator(a.stop).first.click(timeout=2_000)
                    self._finish(session_id, S.FAILED.value, "generation_timeout", answer=rt.partial)
                    return
                await asyncio.sleep(self.poll)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the page closed or crashed
            self._finish(session_id, S.FAILED.value, f"monitor_failed: {type(e).__name__}", answer=rt.partial)

    def _finish(self, session_id: int, to: str, error: str | None, answer: str | None = None,
                cancelled: bool = False) -> None:
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            if answer:
                s.history = [*s.history, {"role": "assistant", "text": redact(answer), "cancelled": cancelled,
                                          "at": datetime.now(UTC).isoformat()}]
            if s.state != to or to == S.AUTH_REQUIRED.value:
                transition(db, s, to, error)
            db.commit()

    async def cancel(self, session_id: int) -> dict[str, Any]:
        rt = self._live(session_id)
        with self._db() as db:
            if db.get(BrowserSession, session_id).state != S.GENERATING.value:
                raise BridgeError("not_generating")
        rt.cancelled = True
        if rt.monitor and not rt.monitor.done():
            rt.monitor.cancel()
        with contextlib.suppress(Exception):
            await rt.page.locator(rt.adapter.stop).first.click(timeout=3_000)
        await asyncio.sleep(self.poll)
        with contextlib.suppress(Exception):
            n = await rt.page.locator(rt.adapter.response).count()
            if n:
                rt.partial = await rt.page.locator(rt.adapter.response).nth(n - 1).inner_text()
        self._finish(session_id, S.READY.value, None, answer=rt.partial, cancelled=True)
        return self.snapshot(session_id)

    # ---------------- read ----------------
    def snapshot(self, session_id: int, state: str | None = None, error: str | None = None) -> dict[str, Any]:
        with self._db() as db:
            s = db.get(BrowserSession, session_id)
            if s is None:
                raise BridgeError("not_found", 404)
            rt = self._rt.get(session_id)
            return {"id": s.id, "provider": s.provider_key, "profile": s.profile_name, "state": state or s.state,
                    "error": error if state else s.error, "live": rt is not None,
                    "partial": redact(rt.partial) if rt and s.state == S.GENERATING.value else None,
                    "turns": len(s.history or []), "last_activity_at": s.last_activity_at}

    def health(self) -> dict[str, Any]:
        return {"live_sessions": len(self._rt), "max_sessions": get_settings().browser_max_sessions,
                "playwright": self._pw is not None, "experimental": True}


_bridge: Bridge | None = None


def get_bridge() -> Bridge:
    global _bridge
    if _bridge is None:
        _bridge = Bridge(base_url=get_settings().platform_internal_url)
    return _bridge
