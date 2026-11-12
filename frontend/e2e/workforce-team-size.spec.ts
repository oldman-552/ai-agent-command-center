import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

// ADR-036: the team grows beyond the default four (up to 20) and shrinks again by archiving; the Manager can run
// through Arena and has its own window. Tier: local integration — real API, no AI and no site contacted.
test.skip(!PASS, "E2E_PASSWORD not set");

const W = "/api/workforce";
const FAKE_URL = process.env.E2E_FAKE_BRIDGE_URL ?? "";
const FAKE_KEY = process.env.E2E_FAKE_BRIDGE_KEY ?? "";

async function send(page: Page, method: "POST" | "DELETE" | "PATCH", path: string, data?: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  return page.request.fetch(path, { method, data, headers: { "X-CSRF-Token": csrf } });
}

async function archiveExtras(page: Page) {
  const force = (await (await page.request.get(W)).json()).workforce;
  for (const w of force.workers) if (w.slot > 4 && w.busy_tasks === 0) await send(page, "DELETE", `${W}/workers/${w.id}`);
}

for (const lang of ["ar", "en"] as const) {
  test(`add a worker, see it in the room, archive it (${lang})`, async ({ page }) => {
    const en = lang === "en";
    await login(page, lang);
    await archiveExtras(page);
    await page.goto("/workforce/workers");
    await expect(page.getByTestId("wf-workers-count")).toContainText(en ? "4 of 20" : "4 من 20");
    await page.getByTestId("wf-worker-add").click();
    const form = page.getByTestId("wf-worker-add-form");
    await form.getByLabel(en ? "Name" : "الاسم").fill(`Translator ${lang}`);
    await form.getByLabel(en ? "Specialty" : "التخصص", { exact: true }).selectOption("custom");
    await form.locator("#wf-add-role").fill("translation");
    await page.getByTestId("wf-worker-add-save").click();
    await expect(page.getByTestId("wf-workers-count")).toContainText(en ? "5 of 20" : "5 من 20");
    const card = page.locator("[data-testid^=wf-worker-card-]").filter({ hasText: `Translator ${lang}` });
    await expect(card).toBeVisible();
    await expect(card).toContainText("translation");
    const slot = (await card.getAttribute("data-testid"))!.split("-").pop();

    // the room seats it with the others
    await page.goto("/workforce/room");
    await expect(page.getByTestId(`room-worker-${slot}`)).toBeVisible();

    await page.goto("/workforce/workers");
    await page.getByTestId(`wf-worker-archive-${slot}`).click();
    await page.getByRole("dialog").getByRole("button", { name: en ? "Archive" : "أرشفة" }).click();
    await expect(page.getByTestId(`wf-worker-card-${slot}`)).toHaveCount(0);
    await expect(page.getByTestId("wf-workers-count")).toContainText(en ? "4 of 20" : "4 من 20");
  });

  test(`many workers fit the room on a wide screen and on a phone (${lang})`, async ({ page }) => {
    await login(page, lang);
    await archiveExtras(page);
    try {
      for (let i = 0; i < 8; i++) {
        expect((await send(page, "POST", `${W}/workers`, { name: `Extra ${i}`, role: "research" })).status()).toBe(201);
      }
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto("/workforce/room");
      const seats = page.locator("[data-testid^=room-worker-]");
      await expect(seats).toHaveCount(12);
      // no two seats overlap
      const boxes = await seats.evaluateAll((els) => els.map((e) => e.getBoundingClientRect().toJSON()));
      for (let a = 0; a < boxes.length; a++) for (let b = a + 1; b < boxes.length; b++) {
        const A = boxes[a], B = boxes[b];
        const overlap = A.left < B.right - 2 && B.left < A.right - 2 && A.top < B.bottom - 2 && B.top < A.bottom - 2;
        expect(overlap, `seats ${a} and ${b} overlap`).toBe(false);
      }
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(seats).toHaveCount(12);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
    } finally {
      await archiveExtras(page);
    }
  });

  test(`the Manager can be set to an Arena bridge and offers its own window (${lang})`, async ({ page }) => {
    const en = lang === "en";
    await login(page, lang);
    await page.goto("/workforce/workers");
    await page.getByTestId("wf-manager-edit").click();
    await expect(page.getByTestId("wf-manager-browser")).toContainText(en ? "The Manager's own window" : "نافذة خاصة بالمدير");
    await page.getByTestId("wf-manager-open-browser").click();
    // the Browser Bridge is off in this environment: the gate answers before anything opens
    await expect(page.getByRole("status").or(page.getByRole("alert")).filter({ hasText: /bridge_disabled|جسر المتصفح|Browser Bridge/ }).first())
      .toBeVisible();
  });

  test(`the Manager runs through the Arena Local Bridge (${lang})`, async ({ page }) => {
    test.skip(!FAKE_URL || !FAKE_KEY, "E2E_FAKE_BRIDGE_URL / E2E_FAKE_BRIDGE_KEY not set (start devtools/fake_arena_bridge.py)");
    const en = lang === "en";
    await login(page, lang);
    const b = await (await send(page, "POST", `${W}/bridges`, { name: `Manager bridge ${lang}`, base_url: FAKE_URL, api_key: FAKE_KEY })).json();
    const mock = (await (await page.request.get("/api/providers")).json()).find((p: { kind: string }) => p.kind === "mock");
    try {
      expect((await send(page, "POST", `${W}/bridges/${b.id}/terms`, { statement: b.terms.statement })).ok()).toBeTruthy();
      await page.goto("/workforce/workers");
      await page.getByTestId("wf-manager-edit").click();
      await page.getByTestId("wf-m-provider").selectOption(String(b.id));
      await expect(page.getByTestId("wf-manager-bridge-note")).toContainText("agent");
      const model = page.getByTestId("wf-m-model");
      if (await model.evaluate((e) => e.tagName === "SELECT")) await model.selectOption("agent");
      else await model.fill("agent");
      await page.getByTestId("wf-manager-save").click();
      await expect(page.getByTestId("wf-manager-via-bridge")).toBeVisible();
      await expect(page.getByTestId("wf-manager-model")).toContainText(`Manager bridge ${lang}`);
      await expect(page.getByTestId("wf-manager-model")).toContainText(en ? "agent" : "agent");
    } finally {
      await send(page, "PATCH", `${W}/manager`, { provider_id: mock.id, model: "mock-model" });
      await send(page, "DELETE", `${W}/bridges/${b.id}`);
    }
  });
}
