#!/usr/bin/env python3
"""Render progress.txt from docs/acceptance.json.

Progress is computed only from acceptance criteria marked "done" (each with
evidence). Phase % = done criteria / total criteria; overall % = weighted sum.

Usage:
  python scripts/progress.py --note "what happened"
  python scripts/progress.py --done F1="committed 6d40fe7" --done F2="file exists" --note "..."
  python scripts/progress.py --undo F4 --note "regression"
  python scripts/progress.py --check     # print numbers only, write nothing
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE = ROOT / "docs" / "acceptance.json"
PROGRESS = ROOT / "progress.txt"
TZ = ZoneInfo("Europe/Berlin")
HISTORY_HEADER = "UPDATE HISTORY:"


def load() -> dict:
    return json.loads(ACCEPTANCE.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    ACCEPTANCE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def phase_pct(phase: dict) -> float:
    crit = phase["criteria"]
    return 100.0 * sum(c["status"] == "done" for c in crit) / len(crit) if crit else 0.0


def overall_pct(data: dict) -> float:
    total_w = sum(p["weight"] for p in data["phases"])
    if total_w != 100:
        raise SystemExit(f"Phase weights must total 100, got {total_w}")
    return sum(p["weight"] * phase_pct(p) / 100.0 for p in data["phases"])


def current_phase(data: dict) -> str:
    for p in data["phases"]:
        if phase_pct(p) < 100:
            return p["name"]
    return "Complete"


def find(data: dict, cid: str) -> dict:
    for p in data["phases"]:
        for c in p["criteria"]:
            if c["id"] == cid:
                return c
    raise SystemExit(f"Unknown criterion id: {cid}")


def old_history() -> list[str]:
    if not PROGRESS.exists():
        return []
    lines = PROGRESS.read_text(encoding="utf-8").splitlines()
    if HISTORY_HEADER not in lines:
        return []
    idx = lines.index(HISTORY_HEADER)
    return [ln for ln in lines[idx + 1:] if ln.strip()]


def render(data: dict, now: str, history: list[str], recent: list[str]) -> str:
    out: list[str] = []
    w = out.append
    w("AI AGENT COMMAND CENTER")
    w("PROJECT DEVELOPMENT PROGRESS")
    w("")
    w(f"Last Updated: {now}")
    w(f"Overall Completion: {overall_pct(data):.0f}%")
    w("(Computed only from verified acceptance criteria in docs/acceptance.json)")
    w("")
    w("CURRENT PHASE:")
    w(current_phase(data))
    w("")
    w("PHASE PROGRESS:")
    w("")
    for p in data["phases"]:
        done = sum(c["status"] == "done" for c in p["criteria"])
        w(f"{p['id']}. {p['name']} (weight {p['weight']}%): {phase_pct(p):.0f}%  [{done}/{len(p['criteria'])}]")
    w("")
    w("COMPLETED TASKS:")
    for p in data["phases"]:
        for c in p["criteria"]:
            if c["status"] == "done":
                w(f"- [{c['id']}] {c['text']} — evidence: {c['evidence']}")
    w("")
    w("IN PROGRESS:")
    for item in data.get("in_progress", []):
        w(f"- {item}")
    w("")
    w("PENDING TASKS:")
    for p in data["phases"]:
        for c in p["criteria"]:
            if c["status"] != "done":
                w(f"- [{c['id']}] {c['text']}")
    w("")
    w("SUCCESSFUL TESTS:")
    for t in data["tests"].get("passed", []):
        w(f"- {t}")
    w("")
    w("FAILED TESTS:")
    for t in data["tests"].get("failed", []):
        w(f"- {t}")
    w("")
    w("BLOCKERS:")
    for b in data.get("blockers", []):
        w(f"- {b}")
    w("")
    w("REQUIRES ADMIN APPROVAL:")
    for a in data.get("requires_approval", []):
        w(f"- {a}")
    w("")
    w("RECENTLY COMPLETED:")
    for r in recent:
        w(f"- {r}")
    w("")
    w("NEXT STEP:")
    w(data.get("next_step", ""))
    w("")
    w(HISTORY_HEADER)
    w("")
    out.extend(history)
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--done", action="append", default=[], help="ID=evidence")
    ap.add_argument("--undo", action="append", default=[], help="ID")
    ap.add_argument("--note", default="")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    data = load()
    recent: list[str] = []
    for item in args.done:
        cid, _, evidence = item.partition("=")
        if not evidence.strip():
            raise SystemExit(f"--done {cid} requires evidence (ID=evidence)")
        c = find(data, cid.strip())
        c["status"], c["evidence"] = "done", evidence.strip()
        recent.append(f"[{c['id']}] {c['text']}")
    for cid in args.undo:
        c = find(data, cid.strip())
        c["status"], c["evidence"] = "pending", ""

    pct = overall_pct(data)
    if args.check:
        print(f"overall={pct:.1f}%")
        for p in data["phases"]:
            print(f"  {p['id']:>2} {p['name']}: {phase_pct(p):.0f}%")
        return 0

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    history = old_history()
    note = args.note or "Progress update"
    history.append(f"- {now} — {note} — {pct:.0f}%")
    save(data)
    PROGRESS.write_text(render(data, now, history, recent), encoding="utf-8")
    print(f"progress.txt updated: {pct:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
