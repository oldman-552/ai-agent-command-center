"""TEST ONLY — deterministic Manager answers for the built-in mock provider. Never real AI.

They let the whole workforce flow run in tests and demos without a model. Every text they produce is labelled
`[MOCK]`, and the platform marks every Manager step, review and report that came from the mock provider as mock.

Markers a test can put into an objective or a result:

    objective  `2. Title [skill] (after 1) {critical.operation} #code <a.py,b.py> @W3`   numbered lines become the
               plan (`#code` makes a development task, `<…>` the repository files its worker is shown)
               [[mock:analysis-garbage]]  [[mock:plan-garbage]]  [[mock:plan-cycle]]  [[mock:plan-unknown-worker]]
               [[mock:plan-bad-once]]     (invalid first, valid after the correction request)
    result     [[mock:review-fail]]  [[mock:review-fail-once]]  [[mock:review-reject]]  [[mock:review-escalate]]
               [[mock:review-approve-unmet]]  [[mock:review-no-evidence]]  [[mock:review-garbage]]
"""
from __future__ import annotations

import json
import re

from app.connectivity.gateway.adapters import MOCK_RESPONDERS
from app.connectivity.gateway.base import ChatRequest
from app.workforce.prompts import ANALYZE_MARKER, PLAN_MARKER, REPORT_MARKER, REVIEW_MARKER

_LINE = re.compile(r"^\s*(\d+)[.)]\s*(.+?)\s*(?:\[([\w,\s-]+)\])?\s*(?:\(after\s*([\d,\s]+)\))?"
                   r"\s*(?:\{([\w.]+)\})?\s*(#code)?\s*(?:<([\w./,\s-]+)>)?\s*(?:@(W\d+))?\s*$")
_ROSTER = re.compile(r"^- (W\d+) \| (.*?) \| role: (.*?) \| skills: (.*?) \| mode: (\w+)\s*$", re.MULTILINE)
_MARK = re.compile(r"\[\[mock:[\w-]+\]\]")
DEFAULT_STEPS = (("research", "Research"), ("engineering", "Build"), ("qa", "Check"), ("documentation", "Document"))


def _last(req: ChatRequest) -> str:
    return str(req.messages[-1]["content"]) if req.messages else ""


def _objective(prompt: str) -> str:
    return prompt.split("OBJECTIVE:\n", 1)[-1].split("\n\nANALYSIS", 1)[0].split("\n\nYour previous reply", 1)[0]


def analysis(req: ChatRequest) -> str:
    prompt = _last(req)
    obj = _objective(prompt)
    if "[[mock:analysis-garbage]]" in obj:
        return "[MOCK] this is not JSON"
    plain = _MARK.sub("", obj).strip()
    return json.dumps({
        "understanding": f"[MOCK] {plain[:300]}",
        "deliverables": ["[MOCK] one result per planned task"],
        "assumptions": ["[MOCK] deterministic analysis — not real AI"],
        "risks": [], "open_questions": [], "complexity": "low"})


def plan(req: ChatRequest) -> str:
    prompt = _last(req)
    obj = _objective(prompt)
    corrected = "Your previous reply was rejected" in prompt
    if "[[mock:plan-garbage]]" in obj or ("[[mock:plan-bad-once]]" in obj and not corrected):
        return "[MOCK] I would rather write prose than JSON"
    roster = [{"label": m[0], "role": m[2], "skills": [s.strip() for s in m[3].split(",") if s.strip() != "-"],
               "mode": m[4]} for m in _ROSTER.findall(prompt)]
    usable = [w for w in roster if w["mode"] != "DISABLED"] or roster

    def worker_for(skills: list[str]) -> str:
        for w in usable:
            if skills and set(skills) <= {w["role"], *w["skills"]}:
                return w["label"]
        return usable[0]["label"] if usable else "W1"

    tasks = []
    for line in obj.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        n, title, skills, after, critical, code, files, worker = m.groups()
        sk = [x.strip() for x in (skills or "").split(",") if x.strip()]
        tasks.append({"key": f"t{n}", "title": f"[MOCK] {title}", "objective": f"[MOCK] part {n} of the objective",
                      "instructions": f"[MOCK] {title}", "expected_output": "[MOCK] a written result",
                      "required_skills": sk, "worker": worker or worker_for(sk), "priority": 5,
                      "depends_on": [f"t{x.strip()}" for x in (after or "").split(",") if x.strip()],
                      "timeout_seconds": None, "acceptance_criteria": [f"[MOCK] The result addresses: {title}"],
                      "critical_operation": critical, "kind": "code" if code else "text",
                      "files": [f.strip() for f in (files or "").split(",") if f.strip()]})
    if not tasks:  # no numbered lines: one task per specialty, for the workers that exist and are not disabled
        plain = _MARK.sub("", obj).strip()[:160] or "the objective"
        made: dict[str, str] = {}
        for role, verb in DEFAULT_STEPS:
            w = next((x for x in usable if x["role"] == role), None)
            if w is None:
                continue
            key = f"t{len(tasks) + 1}"
            deps = [made[r] for r in (("research",) if role == "engineering" else ("engineering",)
                                      if role in ("qa", "documentation") else ()) if r in made]
            tasks.append({"key": key, "title": f"[MOCK] {verb}: {plain}"[:200], "objective": f"[MOCK] {verb.lower()}",
                          "instructions": f"[MOCK] {verb} — {plain}", "expected_output": "[MOCK] a written result",
                          "required_skills": [role], "worker": w["label"], "priority": 5, "depends_on": deps,
                          "timeout_seconds": None,
                          "acceptance_criteria": [f"[MOCK] The result addresses: {verb.lower()}"],
                          "critical_operation": None})
            made[role] = key
        if not tasks:
            tasks = [{"key": "t1", "title": f"[MOCK] {plain}"[:200], "objective": "[MOCK]",
                      "instructions": f"[MOCK] {plain}", "expected_output": "[MOCK] a written result",
                      "required_skills": [], "worker": worker_for([]), "priority": 5, "depends_on": [],
                      "timeout_seconds": None, "acceptance_criteria": ["[MOCK] The objective is addressed"],
                      "critical_operation": None}]
    if "[[mock:plan-cycle]]" in obj and len(tasks) >= 1:
        tasks[0]["depends_on"] = [tasks[-1]["key"]] if len(tasks) > 1 else [tasks[0]["key"]]
        if len(tasks) > 1:
            tasks[-1]["depends_on"] = [tasks[0]["key"]]
    if "[[mock:plan-unknown-worker]]" in obj:
        tasks[0]["worker"] = "W9"
    return json.dumps({"summary": "[MOCK] deterministic plan — not real AI", "tasks": tasks})


def review(req: ChatRequest) -> str:
    prompt = _last(req)
    crit = prompt.split("ACCEPTANCE CRITERIA:\n", 1)[-1].split("\n\nSUBMISSION", 1)[0]
    n = len([ln for ln in crit.splitlines() if re.match(r"^\s*\d+\.", ln)])
    m = re.search(r"<<<SUBMISSION (\w+)>>>(?:\n\(this block[^\n]*\))?\n(.*)\n<<<END SUBMISSION \1>>>", prompt, re.DOTALL)
    body = m.group(2) if m else ""
    if "[[mock:review-garbage]]" in body:
        return "[MOCK] no verdict, just words"
    quote = _MARK.sub("", body).strip()[:60].strip() if "[[mock:review-no-evidence]]" not in body else ""
    fixed = "REVIEWER REQUESTED CHANGES" in body  # an echo-style worker shows that it received the review note
    if "[[mock:review-reject]]" in body:
        met, decision, feedback = False, "reject", "[MOCK] the result is unusable"
    elif "[[mock:review-escalate]]" in body:
        met, decision, feedback = True, "escalate", "[MOCK] a person has to decide"
    elif "[[mock:review-fail]]" in body or ("[[mock:review-fail-once]]" in body and not fixed):
        met, decision, feedback = False, "revise", "[MOCK] address every acceptance criterion explicitly"
    else:
        met, decision, feedback = True, "approve", ""
    criteria = [{"index": i, "met": met, "evidence": quote if met else ""} for i in range(1, n + 1)]
    if "[[mock:review-approve-unmet]]" in body and criteria:
        criteria[0].update(met=False, evidence="")
        decision = "approve"  # the platform must not let this through
    return json.dumps({"criteria": criteria, "decision": decision, "feedback": feedback,
                       "summary": "[MOCK] deterministic review — not real AI"})


def report(req: ChatRequest) -> str:
    prompt = _last(req)
    counts: dict[str, int] = {}
    for cls in re.findall(r"^- \[([a-z_]+)\] task #", prompt, re.MULTILINE):
        counts[cls] = counts.get(cls, 0) + 1
    parts = ", ".join(f"{n} {c.replace('_', ' ')}" for c, n in sorted(counts.items())) or "no task results"
    return json.dumps({"summary": f"[MOCK] deterministic summary — not real AI. The platform classified: {parts}.",
                       "next_steps": ["[MOCK] read each result in its own class before relying on it"]})


MOCK_RESPONDERS.update({ANALYZE_MARKER: analysis, PLAN_MARKER: plan, REVIEW_MARKER: review, REPORT_MARKER: report})
