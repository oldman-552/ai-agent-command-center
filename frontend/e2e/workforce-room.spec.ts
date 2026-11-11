import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

// The Operations Room (/workforce/room) and WF-14 in the UI: a code task delivered by a person is applied and tested
// by the platform, its check is shown on the worker's screen, and the merge waits in "Needs you".
// Tier: mock. The Manager is the built-in mock provider (deterministic, labelled, not AI). The sandbox test run needs
// Docker on the machine that runs the API; without it the check says the sandbox is unavailable.
test.skip(!PASS, "E2E_PASSWORD not set");

const W = "/api/workforce";
const SLOW = { timeout: 60_000 };
const CODE = "Tax calculator.\n\n```file:tax.py\ndef tax(amount, rate=0.15):\n    return round(amount * rate, 2)\n```\n\n" +
  "```file:test_tax.py\nfrom tax import tax\n\n\ndef test_tax():\n    assert tax(100) == 15.0\n```\n";

async function send(page: Page, method: "POST" | "PATCH", path: string, data?: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  return page.request.fetch(path, { method, data, headers: { "X-CSRF-Token": csrf } });
}

async function prepare(page: Page) {
  for (const o of await (await page.request.get(`${W}/objectives`)).json()) {
    if (o.status === "planning" || o.status === "running") await send(page, "POST", `${W}/objectives/${o.id}/cancel`, { reason: "e2e reset" });
  }
  const force = (await (await page.request.get(W)).json()).workforce;
  for (const w of force.workers) {
    expect((await send(page, "PATCH", `${W}/workers/${w.id}`, { mode: "HUMAN_ASSISTED", provider_id: null, assignee_user_id: null })).ok()).toBeTruthy();
  }
  const mock = (await (await page.request.get(`${W}/options`)).json()).providers.find((p: { kind: string }) => p.kind === "mock");
  expect((await send(page, "PATCH", `${W}/manager`, { provider_id: mock.id, model: "mock-model", review_policy: "manager" })).ok()).toBeTruthy();
  expect((await send(page, "PATCH", `${W}/development`, { enabled: true, test_command: ["python3", "-m", "pytest", "-q"] })).ok()).toBeTruthy();
}

for (const lang of ["ar", "en"] as const) {
  test(`operations room — live map, worker screen, summary and big screen (${lang})`, async ({ page }) => {
    test.setTimeout(150_000);
    await login(page, lang);
    await prepare(page);
    const prompt = "Room:\n1. Add the tax calculator [engineering] #code <README.md> @W2\n2. Write the guide [documentation] (after 1) @W4";
    const o = await (await send(page, "POST", `${W}/objectives`, { prompt, title: `Room ${lang}` })).json();
    await expect.poll(async () => (await (await page.request.get(`${W}/objectives/${o.id}`)).json()).tasks.length, SLOW).toBe(2);

    await page.goto("/workforce/room");
    const room = page.getByTestId("ops-room");
    await expect(room).toBeVisible();
    await expect(page.getByTestId("room-worker-2")).toBeVisible();
    await expect(room.getByText(lang === "ar" ? "الهدف «Room ar»" : "Objective “Room en”")).toBeVisible(SLOW);

    // a person delivers the code; the platform applies it, runs the tests and asks for the merge
    const tasks = (await (await page.request.get(`${W}/objectives/${o.id}`)).json()).tasks as { id: number; kind: string; lifecycle: string }[];
    const code = tasks.find((x) => x.kind === "code")!;
    await expect.poll(async () => (await (await page.request.get(`${W}/tasks/${code.id}`)).json()).lifecycle, SLOW).toBe("WAITING_FOR_ACCEPTANCE");
    expect((await send(page, "POST", `${W}/tasks/${code.id}/accept`)).ok()).toBeTruthy();
    expect((await send(page, "POST", `${W}/tasks/${code.id}/submit`, { content: CODE })).status()).toBe(201);
    await expect.poll(async () => {
      await send(page, "POST", `${W}/objectives/${o.id}/tick`);
      return (await (await page.request.get(`${W}/tasks/${code.id}`)).json()).lifecycle;
    }, SLOW).toBe("APPROVED");

    // the merge request appears in "Needs you"
    await expect(page.getByTestId("room-needs-list").getByText(/task #\d+|#\d+/).first()).toBeVisible(SLOW);
    await axe(page);

    // the worker's screen shows the platform's check of the code
    await page.getByTestId("room-worker-2").click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("+ tax.py")).toBeVisible(SLOW);
    await expect(drawer.getByText(/tests_passed|tests_unavailable/)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    await page.getByRole("tab", { name: lang === "ar" ? "ملخص اليوم" : "Today" }).click();
    await expect(room.getByText(lang === "ar" ? "مهام مكتملة هذا الأسبوع" : "Tasks completed this week")).toBeVisible();
    await page.getByRole("tab", { name: lang === "ar" ? "شاشة كبيرة" : "Big screen" }).click();
    await expect(room.getByText(lang === "ar" ? "فريق العمل — مباشر" : "The team — live")).toBeVisible();
    await axe(page);
    await page.getByRole("tab", { name: lang === "ar" ? "الخريطة الحيّة" : "Live map" }).click();
    await send(page, "POST", `${W}/objectives/${o.id}/cancel`, { reason: "e2e done" });
  });
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}
