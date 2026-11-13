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
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.browser import gate
from app.browser.adapters import Adapter, effective
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
    S.FAILED.value: {S.STARTING.value, S.STOPPED.value, S.READY.value, S.AUTH_REQUIRED.value},
    S.STOPPED.value: {S.STARTING.value},
}
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SENDABLE = {S.READY.value, S.COMPLETED.value}
ACTIVE = {S.STARTING.value, S.AUTH_REQUIRED.value, S.READY.value, S.SENDING.value, S.GENERATING.value,
          S.COMPLETED.value}
MAX_PROMPT = 100_000
SEND_WAIT_S = 120  # how long a typed prompt may take to start answering (incl. a person pressing Send by hand)
# ADR-040 (owner decision): a sign-in kept only by a session cookie (no expiry) is made to last this long inside the
# profile, so the worker does not have to sign in again after every window is closed.
SESSION_COOKIE_DAYS = 14
SIGN_IN_SAVE_S = 60  # how often a live window's session cookies are written into its profile


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


def profile_dir(organization_id: int, profile: str) -> Path:
    """A dedicated, platform-owned browser profile of one person, agent or account — the same profile for every
    site, like a browser profile (cookies and sign-ins of all sites live in it). Anything else is refused."""
    if not PROFILE_RE.match(profile or ""):
        raise BridgeError("invalid_profile_name", 422)
    base = Path(get_settings().browser_profiles_dir).resolve()
    path = (base / str(int(organization_id)) / profile).resolve()
    if base not in path.parents:
        raise BridgeError("invalid_profile_name", 422)
    return path


@dataclass
class Runtime:
    context: Any
    page: Any
    adapter: Adapter
    profile: tuple[int, str] = (0, "")
    partial: str = ""
    monitor: asyncio.Task | None = None
    saver: asyncio.Task | None = None
    cancelled: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Bridge:
    """Owns the live browsers of this process. Use one instance per process (see `get_bridge`)."""

    def __init__(self, base_url: str | None = None, poll: float = 0.25):
        self.base_url = (base_url or "http://127.0.0.1:8000").rstrip("/")
        self.poll = poll
        self.save_every = SIGN_IN_SAVE_S
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
            base = gate.adapter(db, s.organization_id, s.provider_key)
            if base is None:
                raise BridgeError("unknown_provider", 404)
            busy = [x for sid, x in self._rt.items() if sid != session_id and x.profile == (s.organization_id, s.profile_name)]
            if busy:  # a browser profile can be open in one window only
                raise BridgeError("profile_in_use", 409)
            a = effective(base, gate.overrides(db, s.organization_id, s.provider_key))
            pdir = profile_dir(s.organization_id, s.profile_name)
            transition(db, s, S.STARTING.value)
            db.commit()
        try:
            async with self._start_lock:
                pw = await self._playwright()
                pdir.mkdir(parents=True, exist_ok=True, mode=0o700)
                ctx = await pw.chromium.launch_persistent_context(
                    str(pdir), headless=settings.browser_headless, accept_downloads=False,
                    executable_path=settings.browser_executable_path or None,
                    args=["--no-first-run", "--no-default-browser-check", "--disable-sync"])
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            rt = Runtime(context=ctx, page=page, adapter=a, profile=(s.organization_id, s.profile_name))
            self._rt[session_id] = rt
            self._attach_close_handlers(session_id, page, ctx)
            rt.saver = asyncio.create_task(self._keep_sign_in(rt))
            # back to the conversation this window was in, if it had one on the same site (never a new chat)
            with self._db() as db:
                cur = db.get(BrowserSession, session_id)
                ref = cur.conversation_ref or db.scalar(  # the profile's last conversation on this site
                    select(BrowserSession.conversation_ref).where(
                        BrowserSession.organization_id == cur.organization_id,
                        BrowserSession.profile_name == cur.profile_name,
                        BrowserSession.provider_key == cur.provider_key,
                        BrowserSession.conversation_ref.is_not(None)).order_by(BrowserSession.id.desc()).limit(1))
            url = ref if ref and self._same_site(ref, a.url) else self._url(a, options or {})
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
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
            # waiting for a sign-in, or failed while its window is still open (e.g. Send did not go out): a person
            # looked at the window; whatever is on screen now decides. Nothing is typed or sent here.
            if s.state not in (S.AUTH_REQUIRED.value, S.FAILED.value):
                raise BridgeError("not_waiting_for_auth")
            if not (s.state == S.FAILED.value and to == S.FAILED.value):
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
        if rt.saver and not rt.saver.done():
            rt.saver.cancel()
        await self._persist_session_cookies(rt.context)
        with contextlib.suppress(Exception):
            await rt.context.close()

    async def _keep_sign_in(self, rt: Runtime) -> None:
        """ADR-040: periodically write the window's session cookies into its profile, so a sign-in survives a closed
        window (also one closed by hand, which gives no chance to save at close)."""
        while True:
            await asyncio.sleep(self.save_every)
            await self._persist_session_cookies(rt.context)

    @staticmethod
    async def _persist_session_cookies(ctx) -> None:
        """ADR-040: a session cookie (no expiry) is given an expiry of SESSION_COOKIE_DAYS and written back into the
        context, so the profile keeps it on disk. Values are never read out, stored by the platform, or logged: the
        cookies only move from the browser to its own profile directory."""
        try:
            cookies = await ctx.cookies()
            session = [c for c in cookies if c.get("expires", -1) == -1]
            if not session:
                return
            until = time.time() + SESSION_COOKIE_DAYS * 86400
            for c in session:
                c["expires"] = until
            await ctx.add_cookies(session)
        except Exception:  # noqa: BLE001 — a window already gone has nothing to save; never fail the caller
            log.debug("sign-in save skipped: window not available")

    def _attach_close_handlers(self, session_id: int, page, ctx) -> None:
        """Register page/context close and crash listeners so a manually closed window is detected immediately,
        not on the next monitor tick or the next send attempt."""

        def _on_gone(source: str):
            """Called synchronously by Playwright when the page closes or crashes. Schedules the async cleanup."""
            rt = self._rt.get(session_id)
            if rt is None:
                return  # already cleaned up
            log.warning("browser session %d: %s — marking DISCONNECTED", session_id, source)
            asyncio.get_event_loop().call_soon_threadsafe(
                asyncio.ensure_future, self._on_page_gone(session_id, source))

        page.on("close", lambda _p: _on_gone("page_closed"))
        page.on("crash", lambda _p: _on_gone("page_crashed"))
        # context close covers the whole browser profile window being shut
        ctx.on("close", lambda _c: _on_gone("context_closed"))
        # if a new tab opens in the same context (some providers do this), attach handlers to it too
        def _on_new_page(new_page):
            new_page.on("close", lambda _p: _on_gone("page_closed"))
            new_page.on("crash", lambda _p: _on_gone("page_crashed"))
        ctx.on("page", _on_new_page)

    async def _on_page_gone(self, session_id: int, reason: str) -> None:
        """Async handler: cancel any in-flight monitor, remove runtime, mark session DISCONNECTED in DB.

        DISCONNECTED (not FAILED) so the workforce readiness panel shows 'window closed → reopen' rather than
        an error state that needs manual reset before the window can be used again."""
        rt = self._rt.pop(session_id, None)
        if rt is None:
            return  # _close() already ran (e.g. explicit stop) — nothing to do
        if rt.monitor and not rt.monitor.done():
            rt.monitor.cancel()
        if rt.saver and not rt.saver.done():
            rt.saver.cancel()
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            if s is not None and s.state in ACTIVE:
                # partial answer (if any) goes into history so it is not silently lost
                if rt.partial:
                    s.history = [*(s.history or []),
                                 {"role": "assistant", "text": redact(rt.partial), "cancelled": True,
                                  "at": datetime.now(UTC).isoformat()}]
                # DISCONNECTED lets start() reopen it; FAILED would need a manual reset first
                target = S.DISCONNECTED.value if s.state in (S.READY.value, S.COMPLETED.value,
                                                             S.AUTH_REQUIRED.value) else S.FAILED.value
                transition(db, s, target, reason)
            db.commit()

    async def shutdown(self) -> None:
        """Close every open browser window and mark its session DISCONNECTED.

        Called by the worker's graceful-shutdown path (./stop.sh) so that sessions left in
        ACTIVE states are cleaned up instead of appearing stuck the next time the bridge starts.
        DISCONNECTED lets start() reopen them; we do not use STOPPED here because DISCONNECTED
        carries the same 'can be reopened' meaning while keeping the history intact.
        """
        for sid in list(self._rt):
            await self._close(sid)
            with self._db() as db:
                s = db.get(BrowserSession, sid)
                if s is not None and s.state in ACTIVE:
                    # Use direct assignment: not every ACTIVE sub-state has a valid TRANSITIONS
                    # edge to DISCONNECTED (e.g. SENDING has no such edge), and shutdown must
                    # succeed unconditionally.  We log it as a warning for visibility.
                    log.warning("browser session %d: bridge shutdown — marking DISCONNECTED (was %s)", sid, s.state)
                    s.state, s.error, s.last_activity_at = (
                        S.DISCONNECTED.value, "bridge shut down", datetime.now(UTC))
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
                await self._type(page, box, prompt)
                await self._submit(page, box, a, before)
                rt.partial, rt.cancelled = "", False
                # the site may need longer, or a person may press Send by hand in the open window: wait for it
                deadline = asyncio.get_running_loop().time() + SEND_WAIT_S
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

    @staticmethod
    async def _box_text(box) -> str:
        """What is still in the prompt box (a textarea's value, or a rich editor's text)."""
        with contextlib.suppress(Exception):
            return (await box.input_value(timeout=1_000)).strip()
        with contextlib.suppress(Exception):
            return (await box.inner_text(timeout=1_000)).strip()
        return ""

    async def _type(self, page, box, prompt: str) -> None:
        """Put the prompt in the box so the site's editor registers it as typed (rich editors — ProseMirror, Lexical,
        React-controlled textareas — ignore a value set from outside, which leaves Send disabled)."""
        await box.click()
        with contextlib.suppress(Exception):
            await box.fill("")
        await page.keyboard.insert_text(prompt)
        if not await self._box_text(box):  # some editors drop inserted text: fall back to fill()
            await box.fill(prompt)
        await asyncio.sleep(0.3)

    async def _submit(self, page, box, a: Adapter, before: int) -> None:
        """Press Send once it is enabled; if the text is still in the box (nothing sent), press Enter. Never sends
        a second time once anything shows that the first one went out."""
        async def went_out() -> bool:
            return (not await self._box_text(box) or await page.locator(a.response).count() > before
                    or await self._visible(page, a.generating))
        btn = page.locator(a.send).first
        for _ in range(20):  # up to ~5 s for the button to become enabled
            if await self._visible(page, a.send) and await btn.is_enabled():
                with contextlib.suppress(Exception):
                    await btn.click(timeout=3_000)
                break
            await asyncio.sleep(0.25)
        for _ in range(8):
            if await went_out():
                return
            await asyncio.sleep(0.25)
        with contextlib.suppress(Exception):
            await box.press("Enter")

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
                    self._remember_conversation(session_id, page.url, a)
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

    def _remember_conversation(self, session_id: int, url: str, a: Adapter) -> None:
        """The conversation the window is in (its address), so a restarted window reopens it, not a new chat."""
        if a.key == "test" or not self._same_site(url, a.url):
            return
        with self._db() as db:
            s = db.get(BrowserSession, session_id, with_for_update=True, populate_existing=True)
            if s is not None and s.conversation_ref != url[:255]:
                s.conversation_ref = url[:255]
                db.commit()

    @staticmethod
    def _same_site(url: str, base: str) -> bool:
        from urllib.parse import urlsplit
        u, b = urlsplit(url), urlsplit(base)
        return u.scheme == "https" and u.hostname is not None and u.hostname == b.hostname and len(url) <= 255

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
