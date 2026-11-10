"""Client for the Browser Bridge service, used by the platform API, the `browser` engine and MCP tools.

`LOCAL` can hold an in-process `Bridge` (tests, single-process setups); otherwise calls go over HTTP to
`BROWSER_BRIDGE_URL` with the internal token."""
from __future__ import annotations

from typing import Any

import httpx

from app.browser.bridge import Bridge, BridgeError
from app.config import get_settings

LOCAL: Bridge | None = None
HTTP_CLIENT: httpx.AsyncClient | None = None


async def call(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    if LOCAL is not None:
        return await _local(method, path, body or {})
    from app.browser.server import internal_token
    client = HTTP_CLIENT or httpx.AsyncClient(timeout=60)
    try:
        r = await client.request(method, get_settings().browser_bridge_url.rstrip("/") + path, json=body,
                                 headers={"Authorization": f"Bearer {internal_token()}"})
    except httpx.HTTPError as e:
        raise BridgeError("bridge_unreachable", 503) from e
    finally:
        if HTTP_CLIENT is None:
            await client.aclose()
    if r.status_code >= 400:
        try:
            code = r.json().get("detail", "error")
        except ValueError:
            code = "error"
        raise BridgeError(str(code), r.status_code)
    return r.json()


async def _local(method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    b = LOCAL
    if path == "/health":
        return b.health()
    if method == "DELETE" and path.startswith("/profiles/"):
        from app.browser.server import delete_profile
        _, _, org, name = path.split("/", 3)
        return await delete_profile(int(org), name)
    parts = path.strip("/").split("/")  # sessions/<id>[/<op>]
    sid = int(parts[1])
    op = parts[2] if len(parts) > 2 else ""
    if op == "start":
        return await b.start(sid, body.get("options", {}))
    if op == "prompt":
        return await b.send(sid, body["prompt"])
    if op == "conversation":
        return await b.new_conversation(sid)
    return await {"recheck": b.recheck, "stop": b.stop, "cancel": b.cancel}[op](sid) if op else b.snapshot(sid)
