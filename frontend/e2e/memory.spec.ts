import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

async function apiPost<T>(page: Page, path: string, body: unknown): Promise<T> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const r = await page.request.post(path, { data: body, headers: { "X-CSRF-Token": csrf } });
  expect(r.status(), await r.text()).toBeLessThan(300);
  return r.json();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

for (const lang of ["ar", "en"] as const) {
  test(`memory: add, search, verify, correct with history, delete; context preview; axe (${lang})`, async ({ page }) => {
    const en = lang === "en";
    await login(page, lang);
    const suffix = `${lang}-${Date.now()}`;
    const project = await apiPost<{ id: number }>(page, "/api/projects", { name: `Memory E2E ${suffix}` });
    // an unverified memory, as an agent would leave it
    const assumed = await apiPost<{ id: number }>(page, "/api/memory", {
      memory_type: "codebase", project_id: project.id, verification: "assumption",
      title: `Test runner ${suffix}`, content: "Tests run with pytest -q inside the sandbox.",
    });

    await page.goto("/memory");
    await page.locator("#m-project").selectOption(String(project.id));
    const card = page.getByTestId(`mem-${assumed.id}`);
    await expect(card.getByTestId("mem-verification")).toHaveText(en ? "Assumption" : "افتراض");

    // a human adds a verified memory through the UI
    await page.getByTestId("mem-add").click();
    await page.locator("#md-type").selectOption("technical_decision");
    await page.locator("#md-title").fill(`Auth ${suffix}`);
    await page.locator("#md-content").fill("Login uses JWT access tokens in HttpOnly cookies with CSRF double submit.");
    await page.getByTestId("md-save").click();
    await expect(page.getByTestId("mem-list").getByText(`Auth ${suffix}`)).toBeVisible();

    // semantic search ranks the relevant memory first
    await page.locator("#m-q").fill("how does login authentication work");
    await page.locator("#m-q").press("Enter");
    await expect(page.getByTestId("mem-list").locator("li").first()).toContainText(`Auth ${suffix}`);
    await page.getByRole("button", { name: en ? "Clear search" : "مسح البحث" }).click();

    // verify the agent's assumption
    await card.getByRole("button", { name: en ? "Mark verified" : "تأكيد التحقق" }).click();
    await expect(card.getByTestId("mem-verification")).toHaveText(en ? "Verified" : "متحقق منه");

    // correct it: a new version, the old one kept as history
    await card.getByRole("button", { name: new RegExp(en ? "^Correct" : "^تصحيح") }).click();
    await page.locator("#md-content").fill("Tests run with pytest -q and Playwright inside the sandbox.");
    await page.getByTestId("md-save").click();
    const corrected = page.getByTestId("mem-list").locator("li", { hasText: `Test runner ${suffix}` });
    await expect(corrected).toContainText("Playwright");
    await expect(corrected).toContainText(en ? "version 2" : "الإصدار 2");
    await corrected.getByRole("button", { name: new RegExp(en ? "^Correct" : "^تصحيح") }).click();
    await expect(page.getByRole("dialog").getByText("Tests run with pytest -q inside the sandbox.")).toBeVisible(); // history
    await axe(page);
    await page.keyboard.press("Escape");

    // delete it (all versions)
    await corrected.getByRole("button", { name: new RegExp(en ? "^Delete" : "^حذف") }).click();
    await page.getByRole("dialog").getByRole("button", { name: en ? "Delete" : "حذف" }).click();
    await expect(page.getByTestId("mem-list").getByText(`Test runner ${suffix}`)).toHaveCount(0);

    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    await axe(page);

    // context preview for a task that exists from earlier runs (if any)
    const tasks = (await (await page.request.get("/api/tasks")).json()) as { id: number; project_id: number | null }[];
    const task = tasks.find((x) => x.project_id);
    test.info().annotations.push({ type: "context-preview", description: task ? `task ${task.id}` : "no task available, skipped" });
    if (task) {
      await page.locator("#m-project").selectOption(String(task.project_id));
      await page.locator("#ctx-task").selectOption(String(task.id));
      const preview = page.getByTestId("ctx-preview");
      await expect(preview).toBeVisible();
      await expect(preview.getByRole("cell", { name: en ? "Requirements" : "المتطلبات" })).toBeVisible();
      await preview.locator("summary").click();
      await expect(preview.locator("pre").first()).toContainText("PLATFORM POLICY");
      await axe(page);
    }
  });
}

test("search mode: choose an embedding model, see where text goes, index, turn off (English)", async ({ page }) => {
  await login(page, "en");
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const name = `E2E local embed ${Date.now()}`;
  const r = await page.request.post("/api/providers", { headers: { "X-CSRF-Token": csrf },
    data: { name, kind: "openai_compatible", base_url: "http://127.0.0.1:9/v1" } });  // nothing listens there
  expect(r.status(), await r.text()).toBe(201);
  await page.goto("/memory");
  const card = page.getByTestId("search-mode");
  await expect(card.getByTestId("sm-status")).toHaveText("Local index");
  await expect(card.getByTestId("sm-save")).toBeDisabled();
  await card.locator("#sm-provider").selectOption({ label: name });
  await expect(card.getByTestId("sm-egress")).toContainText(name);
  await card.locator("#sm-model").fill("nomic-embed-text");
  await card.getByTestId("sm-save").click();
  await expect(card.getByTestId("sm-status")).toHaveText("Embedding model");
  await expect(card.getByTestId("sm-pending")).toBeVisible();
  await card.getByTestId("sm-reindex").click();  // the server is unreachable: reported, nothing breaks
  await expect(page.getByText(/0 indexed, \d+ failed\./)).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);
  await page.getByTestId("search-mode").getByTestId("sm-off").click();
  await expect(card.getByTestId("sm-status")).toHaveText("Local index");
  // search keeps working on the local index
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
});
