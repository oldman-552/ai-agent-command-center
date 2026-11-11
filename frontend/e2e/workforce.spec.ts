import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, request, test } from "@playwright/test";
import { login, PASS } from "./helpers";

// WF-16 / WF-09: the AI Workforce pages, in Arabic and English, with axe on every view.
// Tier: mock. The Manager is the built-in mock provider (deterministic, labelled, not AI). The second test talks
// to the FAKE Arena Local Bridge (E2E_FAKE_BRIDGE_URL / E2E_FAKE_BRIDGE_KEY) and is skipped without it.
// Nothing here is evidence of a real Claude model or a real Arena account.
test.skip(!PASS, "E2E_PASSWORD not set");

const FAKE_URL = process.env.E2E_FAKE_BRIDGE_URL ?? "";
const FAKE_KEY = process.env.E2E_FAKE_BRIDGE_KEY ?? "";
const W = "/api/workforce";
const SLOW = { timeout: 45_000 };

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

async function send(page: Page, method: "POST" | "PATCH" | "DELETE" | "PUT", path: string, data?: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  return page.request.fetch(path, { method, data, headers: { "X-CSRF-Token": csrf } });
}

/** A known state: no open objective, four Human-Assisted workers without a provider, no E2E bridge. */
async function reset(page: Page, opts: { manager: "mock" | "none" }) {
  for (const o of await (await page.request.get(`${W}/objectives`)).json()) {
    if (o.status === "planning" || o.status === "running") await send(page, "POST", `${W}/objectives/${o.id}/cancel`, { reason: "e2e reset" });
  }
  const force = (await (await page.request.get(W)).json()).workforce;
  for (const w of force.workers) {
    const r = await send(page, "PATCH", `${W}/workers/${w.id}`, { mode: "HUMAN_ASSISTED", provider_id: null, fallback_to_human: true, assignee_user_id: null });
    expect(r.ok()).toBeTruthy();
  }
  for (const b of (await (await page.request.get(`${W}/bridges`)).json()).bridges) {
    if (String(b.name).startsWith("E2E ")) expect((await send(page, "DELETE", `${W}/bridges/${b.id}`)).status()).toBe(204);
  }
  const options = await (await page.request.get(`${W}/options`)).json();
  const mock = options.providers.find((p: { kind: string }) => p.kind === "mock");
  expect(mock, "the built-in mock provider exists").toBeTruthy();
  const patch = opts.manager === "mock" ? { provider_id: mock.id, model: "mock-model" } : { provider_id: null };
  expect((await send(page, "PATCH", `${W}/manager`, { ...patch, review_policy: "manager", max_revisions: 2 })).ok()).toBeTruthy();
  return { mockId: mock.id as number, force };
}

/** Another person with the approval right decides: the author of a critical result cannot approve it. */
async function approveAsSecondPerson(page: Page, baseURL: string, approvalId: number) {
  const username = "e2e-approver";
  const password = "e2e-approver-password-1";
  const created = await send(page, "POST", "/api/users", { username, password, role: "ORGANIZATION_ADMIN", language: "en" });
  expect([201, 409], await created.text()).toContain(created.status());  // 409: created by an earlier run
  const api = await request.newContext({ baseURL });
  try {
    expect((await api.post("/api/auth/login", { data: { username, password } })).ok()).toBeTruthy();
    const csrf = (await api.storageState()).cookies.find((c) => c.name === "acc_csrf")?.value ?? "";
    const r = await api.post(`/api/approvals/${approvalId}/decide`, { data: { decision: "approve", reason: "" }, headers: { "X-CSRF-Token": csrf } });
    expect(r.status(), await r.text()).toBe(200);
  } finally {
    await api.dispose();
  }
}

const dialog = (page: Page) => page.getByTestId("wf-task-dialog");
const state = (page: Page) => dialog(page).getByTestId("wf-lifecycle").first();

for (const lang of ["ar", "en"] as const) {
  const en = lang === "en";

  test(`workforce: one prompt → plan → people do the work → review → report, with axe (${lang})`, async ({ page }) => {
    test.setTimeout(240_000);
    await login(page, lang);
    const { mockId } = await reset(page, { manager: "none" });

    // without a Manager model nothing can be submitted, and the page says why
    await page.goto("/workforce");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(en ? "AI Workforce" : "فريق العمل الذكي");
    await expect(page.getByTestId("wf-manager-missing")).toBeVisible();
    await expect(page.getByTestId("wf-submit-objective")).toBeDisabled();
    await expect(page.getByTestId("wf-worker-1").getByTestId("wf-mode")).toHaveAttribute("data-mode", "HUMAN_ASSISTED");
    await expect(page.getByTestId("wf-no-bridge")).toBeVisible();
    await axe(page);

    // choose the Manager's model on the workers page (the mock provider: labelled, never mistaken for AI)
    await page.getByTestId("wf-tabs").getByRole("link", { name: en ? "Manager & workers" : "المدير والعمّال" }).click();
    await expect(page.getByTestId("wf-manager-model")).toContainText(en ? "No model chosen" : "لم يُختر نموذج");
    await axe(page);
    await page.getByTestId("wf-manager-edit").click();
    await page.getByTestId("wf-m-provider").selectOption(String(mockId));
    await page.getByTestId("wf-m-model").selectOption("mock-model");
    await axe(page);
    await page.getByTestId("wf-manager-save").click();
    await expect(page.getByTestId("wf-manager-model")).toContainText("mock-model");
    await expect(page.getByTestId("wf-manager-card").getByTestId("wf-mock")).toBeVisible();

    // one prompt
    await page.goto("/workforce");
    await expect(page.getByTestId("wf-manager-mock")).toBeVisible();
    await page.getByTestId("wf-prompt").fill("1. Research part [research]\n2. Build part [engineering] (after 1)");
    await page.locator("#wf-title").fill(`E2E ${lang} ${Date.now() % 100000}`);
    await page.getByTestId("wf-submit-objective").click();
    await page.waitForURL(/\/workforce\/objectives\/\d+/);
    const objectiveUrl = page.url().split("?")[0];

    // the worker process plans it: two tasks, the first offered to a person, the second waiting for the first
    const tasks = page.getByTestId("wf-plan-tasks").locator("button");
    await expect(tasks).toHaveCount(2, SLOW);
    await expect(tasks.nth(0).getByTestId("wf-lifecycle")).toHaveAttribute("data-state", "WAITING_FOR_ACCEPTANCE", SLOW);
    await expect(tasks.nth(1).getByTestId("wf-lifecycle")).toHaveAttribute("data-state", "PLANNED");
    await expect(page.getByTestId("wf-analysis")).toContainText("[MOCK]");
    await expect(page.getByTestId("wf-analysis").getByTestId("wf-mock")).toBeVisible();
    await axe(page);

    // task 1: the card shows the exact brief; accept, attach a file, submit
    await tasks.nth(0).click();
    await expect(dialog(page).getByTestId("wf-brief-text")).toContainText("RULES FROM THE PLATFORM");
    await expect(state(page)).toHaveText(en ? "Waiting for a person" : "بانتظار شخص");
    await axe(page);
    await dialog(page).getByTestId("wf-accept").click();
    await expect(dialog(page).getByTestId("wf-result")).toBeVisible();
    await dialog(page).getByTestId("wf-result").fill("Research result: option B is cheaper and simpler.");
    await dialog(page).getByTestId("wf-notes").fill("pasted from my Arena session");
    await dialog(page).getByTestId("wf-files").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("supporting notes") });
    await expect(dialog(page).getByTestId("wf-pending-files")).toContainText("notes.txt");
    await axe(page);
    await dialog(page).getByTestId("wf-submit-result").click();
    await expect(state(page)).toHaveAttribute("data-state", "COMPLETED", SLOW);  // reviewed by the (mock) Manager
    await expect(state(page)).toHaveText(en ? "Completed" : "مكتملة");
    const v1 = dialog(page).getByTestId("wf-submission-1");
    await expect(v1.getByTestId("wf-submission-content")).toHaveText("Research result: option B is cheaper and simpler.");
    await expect(v1.getByTestId("wf-submission-review")).toContainText(en ? "the quoted words are in the result" : "الكلمات المقتبسة موجودة في النتيجة");
    await expect(v1.getByRole("link", { name: "notes.txt" })).toHaveAttribute("href", /\/api\/workforce\/attachments\//);
    await axe(page);
    await page.keyboard.press("Escape");
    await expect(dialog(page)).toBeHidden();

    // task 2 appears in "offered to me" on the board; its brief carries the approved result of task 1 as data
    await expect(tasks.nth(1).getByTestId("wf-lifecycle")).toHaveAttribute("data-state", "WAITING_FOR_ACCEPTANCE", SLOW);
    await page.goto("/workforce/tasks?queue=offered");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(en ? "Tasks" : "المهام");
    const card = page.getByTestId("wf-col-people").locator("button", { hasText: "Build part" });
    await expect(card).toBeVisible(SLOW);
    await expect(page.getByTestId("wf-queue-offered")).toHaveAttribute("aria-pressed", "true");
    await axe(page);
    await card.click();
    await expect(dialog(page).getByTestId("wf-brief-text")).toContainText("Research result: option B is cheaper and simpler.");
    await expect(dialog(page).getByTestId("wf-brief-text")).toContainText("UNTRUSTED DATA");
    await dialog(page).getByTestId("wf-accept").click();
    await dialog(page).getByTestId("wf-result").fill("Built as planned. [[mock:review-escalate]]");
    await dialog(page).getByTestId("wf-submit-result").click();

    // the Manager hands this one to a person: a note is required to ask for changes; approving completes it
    await expect(dialog(page).getByTestId("wf-needs-human")).toBeVisible(SLOW);
    await expect(state(page)).toHaveAttribute("data-state", "UNDER_REVIEW");
    await dialog(page).getByTestId("wf-review-revise").click();
    await expect(dialog(page).getByRole("alert")).toHaveText(en ? "Write what has to change, or why you reject it." : "اكتب ما الذي يجب تغييره، أو سبب الرفض.");
    await axe(page);
    await dialog(page).getByTestId("wf-review-note").fill("fine as it is");
    await dialog(page).getByTestId("wf-review-approve").click();
    await expect(state(page)).toHaveAttribute("data-state", "COMPLETED", SLOW);
    await expect(dialog(page).getByTestId("wf-submission-1").getByTestId("wf-submission-decision")).toContainText(en ? "Accepted by User" : "قبِلها المستخدم");
    await page.keyboard.press("Escape");

    // the consolidated result: classes computed by the platform, everything mock labelled
    await page.goto(`${objectiveUrl}?view=report`);
    await expect(page.getByTestId("wf-objective-status")).toHaveAttribute("data-status", "completed", SLOW);
    await expect(page.getByTestId("wf-report")).toBeVisible(SLOW);
    await expect(page.getByTestId("wf-report-status")).toHaveText(en ? "Every task completed" : "اكتملت كل المهام");
    await expect(page.getByTestId("wf-count-human_approved")).toContainText("1");
    await expect(page.getByTestId("wf-count-worker_proposal")).toContainText("1");
    await expect(page.getByTestId("wf-count-verified")).toContainText("0");  // text-only work is never "verified"
    await expect(page.getByTestId("wf-report").getByTestId("wf-mock").first()).toBeVisible();
    await expect(page.getByTestId("wf-report-facts-summary")).toContainText(en ? "2 of 2 tasks were completed; failed or rejected: 0; not finished: 0." : "اكتملت 2 من 2 مهام");
    await expect(page.getByTestId("wf-report-facts-summary")).toContainText(en ? "did not check the content of any result itself" : "لم تفحص بنفسها محتوى أي نتيجة");
    await expect(page.getByTestId("wf-report-facts-summary")).toContainText(en ? "Accepted by a person: 1. Accepted by the Manager only: 1." : "قبِلها شخص: 1. قبِلها المدير فقط: 1.");
    await expect(page.getByTestId("wf-report-facts")).toContainText("2 of 2 tasks were completed");  // the platform's own record
    await expect(page.getByTestId("wf-report-narrative")).toContainText("[MOCK]");
    await expect(page.getByTestId("wf-report-narrative")).toContainText(en ? "Model text — not evidence" : "نص من نموذج — ليس دليلًا");
    await axe(page);

    // history: every Manager step, and no automatic attempt for work people did
    await page.getByTestId("wf-view-history").click();
    await expect(page.getByTestId("wf-history-steps").locator("tbody tr")).toHaveCount(5, SLOW);  // analyse, plan, 2 reviews, summary
    await expect(page.getByTestId("wf-history").getByTestId("wf-history-execution")).toHaveCount(0);
    await axe(page);

    // the overview lists it, with nothing left waiting for me
    await page.goto("/workforce");
    await expect(page.getByTestId("wf-objectives").locator("a").first().getByTestId("wf-objective-status")).toHaveAttribute("data-status", "completed");
    await expect(page.getByTestId("wf-queue-offered")).toContainText("0");
    await expect(page.getByTestId("wf-recent")).toBeVisible();
    await axe(page);
  });

  test(`workforce: bridge settings, terms gate, diagnostics, an Automatic worker on the FAKE bridge, with axe (${lang})`, async ({ page }) => {
    test.skip(!FAKE_URL || !FAKE_KEY, "E2E_FAKE_BRIDGE_URL / E2E_FAKE_BRIDGE_KEY not set (start devtools/fake_arena_bridge.py)");
    test.setTimeout(240_000);
    await login(page, lang);
    await reset(page, { manager: "mock" });
    const name = `E2E bridge ${lang}`;

    await page.goto("/workforce/bridge");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Arena Local Bridge");
    await expect(page.getByTestId("wf-bridge-warning")).toBeVisible();
    await axe(page);

    // add: the key is write-only
    await page.getByTestId("wf-bridge-add").click();
    await page.getByTestId("wf-b-name").fill(name);
    await page.getByTestId("wf-b-url").fill(FAKE_URL);
    await page.getByTestId("wf-b-key").fill(FAKE_KEY);
    await axe(page);
    await page.getByTestId("wf-b-save").click();
    const cardEl = page.locator("[data-testid^='wf-bridge-card-']", { hasText: name });
    await expect(cardEl).toBeVisible();
    const id = Number((await cardEl.getAttribute("data-testid"))!.replace("wf-bridge-card-", ""));
    await expect(page.getByTestId(`wf-bridge-locked-${id}`)).toBeVisible();  // nothing goes to Arena before the terms
    expect(await page.content()).not.toContain(FAKE_KEY);

    // diagnostics need no acknowledgement and name each step; the terms step is the one that is not done
    await page.getByTestId(`wf-bridge-test-${id}`).click();
    await expect(page.getByTestId("wf-diagnostics")).toBeVisible(SLOW);
    for (const step of ["dns", "tcp", "health", "key", "ready"]) await expect(page.getByTestId(`wf-diag-${step}`)).toHaveAttribute("data-ok", "true");
    await expect(page.getByTestId("wf-diag-terms")).toHaveAttribute("data-ok", "false");
    await expect(page.getByTestId("wf-diag-result")).toHaveText(en ? "— passed" : "— نجح");
    await expect(cardEl.getByTestId("wf-mock")).toBeVisible();  // the platform recognised the fake and labels it
    await expect(page.getByTestId(`wf-bridge-accounts-${id}`).locator("li")).toHaveCount(4);
    await expect(page.getByTestId(`wf-bridge-accounts-${id}`)).toContainText("f***@example.test");
    expect(await page.content()).not.toContain("fake-account-1@example.test");  // account addresses: masked only
    await axe(page);

    // the terms gate: the exact statement, and no confirmation before the box is ticked
    await page.getByTestId(`wf-bridge-ack-${id}`).click();
    await expect(page.getByTestId("wf-terms-statement")).toContainText(FAKE_URL);
    await expect(page.getByTestId("wf-terms-confirm")).toBeDisabled();
    await axe(page);
    await page.getByTestId("wf-terms-check").check();
    await page.getByTestId("wf-terms-confirm").click();
    await expect(page.getByTestId(`wf-bridge-revoke-${id}`)).toBeVisible();
    await expect(page.getByTestId(`wf-bridge-locked-${id}`)).toHaveCount(0);

    // worker 1 becomes Automatic on one session of one account
    await page.goto("/workforce/workers");
    await page.getByTestId("wf-worker-edit-1").click();
    await page.getByTestId("wf-w-mode").selectOption("AUTOMATIC");
    await expect(page.getByTestId("wf-w-mode-help")).toContainText(en ? "sends each task" : "ترسل كل مهمة");
    await expect(page.getByTestId("wf-worker-save")).toBeDisabled();  // Automatic needs a provider and a session
    await page.getByTestId("wf-w-provider").selectOption(String(id));
    await page.getByTestId("wf-w-model").selectOption({ index: 1 });
    await axe(page);
    await page.getByTestId("wf-worker-save").click();
    const w1 = page.getByTestId("wf-worker-card-1");
    await expect(w1.getByTestId("wf-mode")).toHaveAttribute("data-mode", "AUTOMATIC");
    await expect(w1.getByTestId("wf-worker-status")).toContainText(en ? "Ready — works automatically" : "جاهز — يعمل تلقائيًا");
    await expect(w1.getByTestId("wf-worker-binding")).toContainText(en ? "One session of one account" : "جلسة واحدة لحساب واحد");
    await expect(w1.getByTestId("wf-worker-binding")).toContainText("f***@example.test");
    await expect(w1.getByTestId("wf-mock")).toBeVisible();
    await axe(page);

    // one prompt, done by the Automatic worker through the fake bridge, reviewed by the mock Manager
    await page.goto("/workforce");
    await expect(page.getByTestId("wf-accounts")).toBeVisible();
    await page.getByTestId("wf-prompt").fill("1. Research part [research]");
    await page.getByTestId("wf-submit-objective").click();
    await page.waitForURL(/\/workforce\/objectives\/\d+/);
    await expect(page.getByTestId("wf-objective-status")).toHaveAttribute("data-status", "completed", SLOW);
    const task = page.getByTestId("wf-plan-tasks").locator("button").first();
    await expect(task.getByTestId("wf-mode")).toHaveAttribute("data-mode", "AUTOMATIC");
    await task.click();
    await expect(dialog(page).getByTestId("wf-submission-1").getByTestId("wf-submission-content")).toContainText("[FAKE ARENA BRIDGE — not Arena]");
    await expect(dialog(page).getByTestId("wf-submission-1").getByTestId("wf-mock")).toBeVisible();
    await expect(dialog(page).getByTestId("wf-submission-1")).toContainText(en ? "usage not reported by the provider" : "المزوّد لم يذكر الاستهلاك");
    await axe(page);
    await page.keyboard.press("Escape");

    await page.getByTestId("wf-view-report").click();
    await expect(page.getByTestId("wf-count-worker_proposal")).toContainText("1", SLOW);
    await expect(page.getByTestId("wf-report").getByTestId("wf-mock").first()).toBeVisible();
    await page.getByTestId("wf-view-history").click();
    await expect(page.getByTestId("wf-history-execution")).toHaveCount(1, SLOW);
    await expect(page.getByTestId("wf-history-usage")).toHaveText(en ? "not reported" : "لم يُذكر");  // never "0 tokens"
    await axe(page);

    // a bridge that workers still use cannot be deleted; after switching the worker back it can
    expect((await send(page, "DELETE", `${W}/bridges/${id}`)).status()).toBe(409);
    await reset(page, { manager: "mock" });
    await page.goto("/workforce/bridge");
    await expect(page.locator("[data-testid^='wf-bridge-card-']", { hasText: name })).toHaveCount(0);
  });

  test(`workforce: a critical task is completed only in the Approval Center, by another person; board filters (${lang})`, async ({ page, baseURL }) => {
    test.setTimeout(240_000);
    await login(page, lang);
    await reset(page, { manager: "mock" });
    const title = `E2E critical ${lang} ${Date.now() % 100000}`;
    const r = await send(page, "POST", `${W}/objectives`, { title, prompt: "1. Publish the announcement [documentation] {exposure.public}" });
    expect(r.status()).toBe(201);
    const objective = (await r.json()) as { id: number };

    // the board, filtered to this objective: one task, offered to a person, marked as needing an approval
    await page.goto("/workforce/tasks");
    await page.locator("#wf-f-objective").selectOption(String(objective.id));
    await expect(page).toHaveURL(new RegExp(`objective=${objective.id}`));
    const card = page.getByTestId("wf-col-people").locator("button", { hasText: "Publish the announcement" });
    await expect(card).toBeVisible(SLOW);
    await expect(page.getByTestId("wf-task-count")).toHaveText(en ? "Tasks: 1" : "عدد المهام: 1");
    await expect(card).toContainText(en ? "Needs approval when accepted" : "تحتاج موافقة عند القبول");
    // filters narrow what the server returns: by state, by mode, by text — and say so when nothing matches
    await page.locator("#wf-f-state").selectOption("COMPLETED");
    await expect(page.getByTestId("wf-tasks-empty")).toHaveText(en ? "No task matches these filters." : "لا توجد مهمة تطابق هذه التصفية.");
    await axe(page);
    await page.locator("#wf-f-state").selectOption("");
    await page.locator("#wf-f-mode").selectOption("AUTOMATIC");
    await expect(page.getByTestId("wf-tasks-empty")).toBeVisible();
    await page.locator("#wf-f-mode").selectOption("HUMAN_ASSISTED");
    await expect(card).toBeVisible();
    await page.locator("#wf-f-q").fill("no such words here");
    await page.locator("#wf-f-q").press("Enter");
    await expect(page.getByTestId("wf-tasks-empty")).toBeVisible();
    await page.locator("#wf-f-q").fill("announcement");
    await page.locator("#wf-f-q").press("Enter");
    await expect(card).toBeVisible();
    await page.reload();  // filters live in the address, so a reload keeps them
    await expect(page.locator("#wf-f-objective")).toHaveValue(String(objective.id));
    await expect(page.locator("#wf-f-mode")).toHaveValue("HUMAN_ASSISTED");
    await expect(card).toBeVisible(SLOW);

    // a person does the work; the (mock) Manager accepts it, but that does not complete a critical task
    await card.click();
    await dialog(page).getByTestId("wf-accept").click();
    await dialog(page).getByTestId("wf-result").fill("The announcement text, ready to publish.");
    await dialog(page).getByTestId("wf-submit-result").click();
    await expect(state(page)).toHaveAttribute("data-state", "APPROVED", SLOW);
    await expect(state(page)).toHaveText(en ? "Reviewed — awaiting approval" : "روجعت — بانتظار الموافقة");
    const notice = dialog(page).getByTestId("wf-approval");
    await expect(notice).toContainText(en ? "only when a person approves it in the Approval Center" : "إلا عندما يوافق عليها شخص في مركز الموافقات");
    await expect(notice).toContainText(en ? "someone other than the person who submitted the result" : "شخصاً غير الذي قدّم النتيجة");
    await axe(page);

    // the Approval Center: the frozen payload names the version and the fingerprint of the text, and links back
    await notice.getByRole("link").click();
    const ap = page.getByTestId("approval-details");
    await expect(ap).toBeVisible();
    await expect(ap).toContainText(en ? "External public exposure" : "إتاحة عامة خارجية");
    await expect(ap).toContainText("content_sha256");
    await expect(ap.getByTestId("approval-workforce")).toContainText(en ? "version 1 of the task’s result" : "النسخة 1 من نتيجة المهمة");
    await expect(ap.getByTestId("approval-task-link")).toHaveAttribute("href", /\/workforce\/tasks\?task=\d+$/);
    await expect(ap.getByTestId("approval-evidence")).toContainText(en ? "Result version" : "نسخة النتيجة");
    await axe(page);

    // the person who submitted the result cannot be the one who approves it: refused, with the reason
    await ap.getByTestId("ap-approve").click();
    await expect(ap.getByRole("alert")).toHaveText(en ? "You cannot decide on a request you made yourself." : "لا يمكنك اتخاذ القرار في طلب قدّمته بنفسك.");
    await axe(page);
    const approvalId = Number(new URL(page.url()).searchParams.get("id"));
    expect(approvalId).toBeGreaterThan(0);
    const before = await (await page.request.get(`${W}/objectives/${objective.id}`)).json();
    expect(before.tasks[0].lifecycle).toBe("APPROVED");  // still not completed
    await approveAsSecondPerson(page, baseURL!, approvalId);

    // only now is the task completed, and the report counts it as approved by a person
    await page.goto(`/workforce/objectives/${objective.id}?view=report`);
    await expect(page.getByTestId("wf-objective-status")).toHaveAttribute("data-status", "completed", SLOW);
    await expect(page.getByTestId("wf-count-human_approved")).toContainText("1", SLOW);
    await expect(page.getByTestId("wf-count-worker_proposal")).toContainText("0");
    await axe(page);
  });

  test(`workforce: no page overflow on a phone or a wide screen (${lang})`, async ({ page }) => {
    await login(page, lang);
    const objectives = (await (await page.request.get(`${W}/objectives`)).json()) as { id: number; status: string }[];
    const done = objectives.find((o) => o.status === "completed");
    const paths = ["/workforce", "/workforce/tasks", "/workforce/workers", "/workforce/bridge"];
    if (done) paths.push(`/workforce/objectives/${done.id}`, `/workforce/objectives/${done.id}?view=report`, `/workforce/objectives/${done.id}?view=history`);
    for (const path of paths) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await page.waitForLoadState("networkidle");
      for (const width of [375, 1440]) {
        await page.setViewportSize({ width, height: 900 });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, `${path} at ${width}px`).toBeLessThanOrEqual(0);
      }
    }
  });
}
