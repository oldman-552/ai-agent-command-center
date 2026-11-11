"""WF-21 — the Manager on a real model. Tier: **REAL PROVIDER**. Skipped unless you switch it on.

This is the only place where the Manager's prompts meet a real model: every other test uses the built-in mock
provider, whose answers are written by hand. **Nobody ran this file against a real model during development.**
(`test_real_tiers_selftest.py` runs the same steps on the mock provider, so the steps themselves are known to work.)

Run it on your own machine — `REAL-TESTS.bat` does this for you, or by hand:

    cd backend
    REAL_MANAGER_API_KEY=<your key> REAL_MANAGER_MODEL=<model id> uv run pytest -m real_provider -v -s

    REAL_MANAGER_KIND       provider kind, default "anthropic" (a Claude model, as the requirement says)
    REAL_MANAGER_BASE_URL   only for a compatible endpoint
    REAL_PROVIDER_WORKERS=1 also run the second test, where the four workers use the same model (more requests)

What it sends: the fixed prompt and the fixed results below — nothing of yours. No Arena and no bridge are involved.
What it costs: the first test makes about five to eight requests (one analysis, one plan, one review per task, one
summary; a repair request when an answer does not validate). The second adds one request per task and per revision.
The key is stored encrypted in the **test** database for the duration of the test and deleted afterwards; it is
never printed. `-s` shows a summary you can keep as evidence.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from app.models import Execution, Provider, Task, Workflow
from app.workforce import control
from tests import wf as h

KEY = os.environ.get("REAL_MANAGER_API_KEY", "")
MODEL = os.environ.get("REAL_MANAGER_MODEL", "")
KIND = os.environ.get("REAL_MANAGER_KIND", "anthropic")
BASE_URL = os.environ.get("REAL_MANAGER_BASE_URL", "")
WORKERS_TOO = os.environ.get("REAL_PROVIDER_WORKERS", "") == "1"
# REAL-TESTS.bat names the tier it wants. Then missing settings are a failure: a run that silently skipped everything
# would end green and be mistaken for evidence.
REQUIRED = os.environ.get("REAL_TIER_REQUIRED", "") == "real_provider"
API = "/api/workforce"

pytestmark = [
    pytest.mark.real_provider,
    pytest.mark.skipif(not (KEY and MODEL) and not REQUIRED,
                       reason="real provider tier: set REAL_MANAGER_API_KEY and REAL_MANAGER_MODEL"),
]

PROMPT = """Plan exactly two small tasks and nothing else.
Task 1, for the research worker: name three common features of a to-do list app, as a numbered list of three items.
Task 2, for the documentation worker, after task 1: write a description of a to-do list app in two sentences that
mentions the three features from task 1.
Each task has one or two acceptance criteria that can be checked by reading the result."""

FEATURES = "1. Add tasks\n2. Mark tasks as done\n3. Set due dates"
DESCRIPTION = ("A to-do list app lets you add tasks and mark tasks as done when you finish them. "
               "It also lets you set due dates, so that nothing is forgotten.")
VERDICTS = {"approve", "revise", "reject", "needs_human"}
BROKEN = ("invalid_verdict", "manager_unavailable")  # the model's answer could not be used at all
OPERATOR_NOTE = "approved by the test operator"


@dataclass(frozen=True)
class Expect:
    """What a step of the Manager must look like. The real tier expects a real model; the self-test the mock."""
    kind: str
    model: str
    is_mock: bool = False


def answer_for(card: dict) -> str:
    """The test plays the person behind a Human-Assisted worker. Two answers fit the two tasks the prompt asks for;
    anything else the model planned gets both, which a careful reviewer may well send back."""
    text = f"{card['title']} {card.get('instructions') or ''}".lower()
    if "description" in text or "sentence" in text:
        return DESCRIPTION
    if "feature" in text:
        return FEATURES
    return f"{FEATURES}\n\n{DESCRIPTION}"


def steps_of(admin_client, wf_id: int) -> list[dict]:
    return admin_client.get(f"{API}/objectives/{wf_id}/history").json()["manager_steps"]


def summarise(admin_client, wf_id: int, title: str, expect: Expect) -> None:
    tier = "MOCK (self-test)" if expect.is_mock else "REAL PROVIDER tier"
    print(f"\n=== {title} — {tier}, {expect.kind} / {expect.model} ===")
    for s in steps_of(admin_client, wf_id):
        usage = f"{s['usage'].get('input_tokens')} in / {s['usage'].get('output_tokens')} out" if s["usage"] else "usage not reported"
        print(f"  {s['kind']:<12} {s['status']:<8} attempt {s['attempt']}  {s['duration_ms']} ms  {usage}"
              + (f"  task #{s['task_id']}" if s["task_id"] else "") + (f"  ERROR {s['error']}" if s["error"] else ""))
    report = admin_client.get(f"{API}/objectives/{wf_id}/report")
    if report.status_code == 200:
        c = report.json()["content"]
        print(f"  report v{report.json()['version']}: {report.json()['status']}; classes {c['counts']}; "
              f"planned by {c['planned_by']}; mock: {c['is_mock']}")


def assert_steps(steps: list[dict], kinds: set[str], expect: Expect) -> None:
    done = [s for s in steps if s["status"] == "ok"]
    missing = kinds - {s["kind"] for s in done}
    assert not missing, f"no successful Manager step of kind {sorted(missing)}: {[(s['kind'], s['status'], s['error']) for s in steps]}"
    for s in done:
        assert s["is_mock"] is expect.is_mock and s["provider"] == expect.kind and s["model"] == expect.model, s
        # a provider that reports usage must not lose it on the way: "not reported" here would be the adapter's fault
        assert s["usage_reported"] and (s["usage"].get("input_tokens") or 0) > 0 and (s["usage"].get("output_tokens") or 0) > 0, s


def finish(db, wf_id: int) -> None:
    db.expire_all()
    wf = db.get(Workflow, wf_id)
    if wf.status in ("planning", "running"):
        control.cancel_objective(db, wf, h.admin(db), "test finished")
        db.commit()


def people_flow(admin_client, db, expect: Expect, prompt: str = PROMPT, secret: str = "") -> dict:
    """One objective with four Human-Assisted workers: the Manager analyses, plans, reviews every result and writes
    the summary on `expect.model`; the test plays the people. Returns what happened, for the caller to print."""
    wf = h.objective(db, prompt)
    outcome = asyncio.run(control.plan(wf.id))
    db.expire_all()
    wf = db.get(Workflow, wf.id)
    try:
        # 1. analysis and plan: the answers passed the platform's validation (workers exist, no cycle, criteria)
        assert outcome == "planned", f"planning ended as {outcome!r}: {wf.error}"
        assert_steps(steps_of(admin_client, wf.id), {"analyze", "plan"}, expect)
        tasks = list(db.scalars(select(Task).where(Task.workflow_id == wf.id).order_by(Task.id)))
        assert 1 <= len(tasks) <= 6, [t.title for t in tasks]
        obj = admin_client.get(f"{API}/objectives/{wf.id}").json()
        assert obj["manager"]["is_mock"] is expect.is_mock and obj["manager"]["model"] == expect.model
        for t in obj["tasks"]:
            assert t["worker"] and t["worker"]["label"] in {"W1", "W2", "W3", "W4"}, t
            assert t["title"].startswith("[MOCK]") is expect.is_mock, t["title"]
        for t in tasks:
            assert t.acceptance_criteria, f"task {t.id} has no acceptance criterion"

        # 2. people do the work; the Manager reviews every result
        verdicts: dict[int, str] = {}
        by_operator: list[int] = []
        for _ in range(len(tasks) * 4 + 4):
            out = h.tick(wf)
            verdicts.update({int(k): v for k, v in (out.get("reviewed") or {}).items()})
            if out.get("finished"):
                break
            for card in admin_client.get(f"{API}/tasks", params={"objective_id": wf.id, "queue": "open"}).json()["tasks"]:
                detail = admin_client.get(f"{API}/tasks/{card['id']}").json()
                if detail["lifecycle"] == "WAITING_FOR_ACCEPTANCE":
                    assert admin_client.post(f"{API}/tasks/{card['id']}/accept").status_code == 200
                    detail = admin_client.get(f"{API}/tasks/{card['id']}").json()
                if detail["can"].get("submit"):
                    r = admin_client.post(f"{API}/tasks/{card['id']}/submit", json={"content": answer_for(detail)})
                    assert r.status_code == 201, r.text
                elif detail["needs_human_review"]:
                    # the Manager handed it to a person, or asked for changes too often: legitimate. An answer the
                    # platform could not read at all is not — that is what this tier exists to catch.
                    assert not (detail["review_reason"] or "").startswith(BROKEN), detail["review_reason"]
                    r = admin_client.post(f"{API}/tasks/{card['id']}/review", json={"decision": "approve", "note": OPERATOR_NOTE})
                    assert r.status_code == 200, r.text
                    by_operator.append(card["id"])
        db.expire_all()
        wf = db.get(Workflow, wf.id)
        assert verdicts and set(verdicts.values()) <= VERDICTS, verdicts
        assert wf.status == "completed", (wf.status, wf.error, h.states(db, wf))

        # 3. every kind of Manager step ran on the expected model, with usage; no worker request was made anywhere
        steps = steps_of(admin_client, wf.id)
        assert_steps(steps, {"analyze", "plan", "review", "consolidate"}, expect)
        assert not [s for s in steps if s["status"] == "invalid" and s["kind"] in ("review", "consolidate")], steps
        assert db.scalar(select(func.count()).select_from(Execution).join(Task, Task.id == Execution.task_id)
                         .where(Task.workflow_id == wf.id)) == 0

        # 4. the report: classes from the records; the summary from the model and labelled with it
        c = admin_client.get(f"{API}/objectives/{wf.id}/report").json()["content"]
        assert c["is_mock"] is expect.is_mock and c["planned_by"]["is_mock"] is expect.is_mock
        assert c["planned_by"]["model"] == expect.model
        assert sum(c["counts"].values()) == len(tasks) and c["counts"]["verified"] == 0
        assert c["narrative"] and c["narrative"]["summary"].strip() and c["narrative"]["written_by"]["model"] == expect.model
        assert c["narrative_error"] is None
        if secret:
            assert secret not in admin_client.get(f"{API}/objectives/{wf.id}/history").text
        return {"workflow_id": wf.id, "tasks": len(tasks), "verdicts": verdicts, "approved_by_operator": by_operator}
    finally:
        summarise(admin_client, wf.id, "Manager with people as workers", expect)
        finish(db, wf.id)


def unattended_flow(admin_client, db, provider_id: int, expect: Expect, prompt: str = PROMPT) -> dict:
    """The same objective with the four workers Automatic on the same provider: one prompt, nobody at the keyboard
    unless the Manager hands a result to a person."""
    for slot in (1, 2, 3, 4):
        h.automatic(db, slot, provider_id, expect.model)
    wf = h.objective(db, prompt)
    outcome = asyncio.run(control.plan(wf.id))
    db.expire_all()
    wf = db.get(Workflow, wf.id)
    try:
        assert outcome == "planned", f"planning ended as {outcome!r}: {wf.error}"
        h.drive(db, wf, rounds=20)
        by_operator: list[int] = []
        for _ in range(8):  # whatever the Manager could not settle goes to a person; the operator accepts it
            waiting = admin_client.get(f"{API}/tasks", params={"objective_id": wf.id, "queue": "review"}).json()["tasks"]
            if not waiting:
                break
            for card in waiting:
                detail = admin_client.get(f"{API}/tasks/{card['id']}").json()
                assert not (detail["review_reason"] or "").startswith(BROKEN), detail["review_reason"]
                r = admin_client.post(f"{API}/tasks/{card['id']}/review", json={"decision": "approve", "note": OPERATOR_NOTE})
                assert r.status_code == 200, r.text
                by_operator.append(card["id"])
            h.drive(db, wf, rounds=6)
        db.expire_all()
        wf = db.get(Workflow, wf.id)
        assert wf.status == "completed", (wf.status, wf.error, h.states(db, wf))
        assert_steps(steps_of(admin_client, wf.id), {"analyze", "plan", "review", "consolidate"}, expect)
        runs = list(db.scalars(select(Execution).join(Task, Task.id == Execution.task_id).where(Task.workflow_id == wf.id)))
        assert runs and all(e.status == "COMPLETED" for e in runs), [(e.id, e.status, e.error) for e in runs]
        for e in runs:  # usage a provider reported is recorded: neither unknown nor zero
            assert e.input_tokens and e.output_tokens, (e.id, e.input_tokens, e.output_tokens)
        c = admin_client.get(f"{API}/objectives/{wf.id}/report").json()["content"]
        assert c["is_mock"] is expect.is_mock and all(i["mode"] == "AUTOMATIC" for i in c["items"])
        return {"workflow_id": wf.id, "executions": len(runs), "approved_by_operator": by_operator}
    finally:
        summarise(admin_client, wf.id, "Manager and workers on the same provider", expect)
        finish(db, wf.id)


@pytest.fixture()
def real_manager(admin_client, db):
    """The Manager on the owner's model; removed again afterwards so that no key stays in the test database."""
    assert KEY and MODEL, "REAL_MANAGER_API_KEY and REAL_MANAGER_MODEL did not reach the test: nothing was checked"
    h.reset(db, manager=None)
    body = {"name": f"Real manager {uuid.uuid4().hex[:6]}", "kind": KIND, "api_key": KEY}
    if BASE_URL:
        body["base_url"] = BASE_URL
    r = admin_client.post("/api/providers", json=body)
    assert r.status_code == 201, r.text
    assert KEY not in r.text  # write-only, as everywhere
    pid = r.json()["id"]
    assert admin_client.post(f"/api/providers/{pid}/models", json={"name": MODEL}).status_code == 201
    r = admin_client.patch(f"{API}/manager", json={"provider_id": pid, "model": MODEL})
    assert r.status_code == 200, r.text
    yield pid
    h.reset(db, manager=None)
    db.expire_all()
    p = db.get(Provider, pid)
    if p is not None:
        db.delete(p)
        db.commit()


def test_the_manager_analyses_plans_reviews_and_summarises_on_a_real_model(admin_client, db, real_manager):
    people_flow(admin_client, db, Expect(KIND, MODEL), secret=KEY)


@pytest.mark.skipif(not WORKERS_TOO, reason="set REAL_PROVIDER_WORKERS=1 to let the four workers use the real model too")
def test_one_prompt_runs_unattended_when_the_workers_use_the_real_model_too(admin_client, db, real_manager):
    unattended_flow(admin_client, db, real_manager, Expect(KIND, MODEL))
