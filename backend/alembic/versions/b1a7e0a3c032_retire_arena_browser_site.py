"""retire the Arena site of the Browser Bridge (ADR-032)

Arena is reached only through Arena Local Bridge. This data migration deletes nothing:

  organizations.settings.browser_bridge
      acks.arena, selectors.arena, sites.<key on an Arena host> (+ their acks/selectors)
          -> moved under  retired_arena  (kept, unused)
  browser_sessions of those sites that were not stopped
          -> STOPPED with an explanatory error (previous state/error remembered under retired_arena.sessions)

Downgrade moves everything back and restores the remembered session error text (a stopped browser cannot be
re-opened by a migration, so sessions stay STOPPED).

Revision ID: b1a7e0a3c032
Revises: 7dd59ff643be
Create Date: 2026-11-11 07:30:00.000000

"""
from __future__ import annotations

import json
from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1a7e0a3c032'
down_revision: str | Sequence[str] | None = '7dd59ff643be'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ARENA_HOST_SUFFIXES = ("arena.ai", "lmarena.ai")
RETIRED_ERROR = "retired: Arena is served only through Arena Local Bridge (ADR-032)"


def _is_arena_url(url: object) -> bool:
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == s or host.endswith("." + s) for s in ARENA_HOST_SUFFIXES)


def retire(cfg: dict) -> tuple[dict, list[str]]:
    """Pure: the `browser_bridge` settings without anything Arena, plus the retired site keys."""
    cfg = deepcopy(cfg or {})
    sites = dict(cfg.get("sites") or {})
    keys = ["arena"] + sorted(k for k, d in sites.items() if isinstance(d, dict) and _is_arena_url(d.get("url")))
    box = dict(cfg.get("retired_arena") or {})
    moved = False
    for section in ("acks", "selectors", "sites"):
        current = dict(cfg.get(section) or {})
        kept = dict(box.get(section) or {})
        for k in keys:
            if k in current:
                kept[k] = current.pop(k)
                moved = True
        if section in cfg or current:
            cfg[section] = current
        if kept:
            box[section] = kept
    if moved:
        box.setdefault("reason", "ADR-032: Arena is served only through Arena Local Bridge")
        box.setdefault("at", datetime.now(UTC).isoformat())
        cfg["retired_arena"] = box
    return cfg, keys


def restore(cfg: dict) -> dict:
    """Pure inverse of `retire` for the settings."""
    cfg = deepcopy(cfg or {})
    box = cfg.pop("retired_arena", None) or {}
    for section in ("acks", "selectors", "sites"):
        back = box.get(section) or {}
        if back:
            cfg[section] = {**(cfg.get(section) or {}), **back}
    return cfg


def upgrade() -> None:
    bind = op.get_bind()
    orgs = bind.execute(sa.text("SELECT id, settings FROM organizations")).fetchall()
    for org_id, settings in orgs:
        settings = dict(settings or {})
        cfg = settings.get("browser_bridge")
        keys = ["arena"]
        if isinstance(cfg, dict):
            new_cfg, keys = retire(cfg)
        else:
            new_cfg = None
        rows = bind.execute(
            sa.text("SELECT id, state, error FROM browser_sessions WHERE organization_id = :o AND provider_key = ANY(:k) "
                    "AND state <> 'STOPPED'"), {"o": org_id, "k": keys}).fetchall()
        if rows:
            new_cfg = new_cfg if new_cfg is not None else {"enabled": False, "acks": {}}
            box = dict(new_cfg.get("retired_arena") or {})
            box.setdefault("reason", "ADR-032: Arena is served only through Arena Local Bridge")
            box.setdefault("at", datetime.now(UTC).isoformat())
            box["sessions"] = {**(box.get("sessions") or {}),
                               **{str(i): {"state": st, "error": err} for i, st, err in rows}}
            new_cfg["retired_arena"] = box
            bind.execute(sa.text("UPDATE browser_sessions SET state = 'STOPPED', error = :e WHERE id = ANY(:ids)"),
                         {"e": RETIRED_ERROR, "ids": [r[0] for r in rows]})
        if new_cfg is not None and new_cfg != cfg:
            settings["browser_bridge"] = new_cfg
            bind.execute(sa.text("UPDATE organizations SET settings = CAST(:s AS jsonb) WHERE id = :o"),
                         {"s": json.dumps(settings), "o": org_id})


def downgrade() -> None:
    bind = op.get_bind()
    orgs = bind.execute(sa.text("SELECT id, settings FROM organizations")).fetchall()
    for org_id, settings in orgs:
        settings = dict(settings or {})
        cfg = settings.get("browser_bridge")
        if not isinstance(cfg, dict) or "retired_arena" not in cfg:
            continue
        for sid, prev in ((cfg["retired_arena"].get("sessions") or {}).items()):
            bind.execute(sa.text("UPDATE browser_sessions SET error = :e WHERE id = :i AND error = :r"),
                         {"e": prev.get("error"), "i": int(sid), "r": RETIRED_ERROR})
        settings["browser_bridge"] = restore(cfg)
        bind.execute(sa.text("UPDATE organizations SET settings = CAST(:s AS jsonb) WHERE id = :o"),
                     {"s": json.dumps(settings), "o": org_id})
