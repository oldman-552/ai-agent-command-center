import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

/** API call from the logged-in page (same cookies + CSRF header as the app). */
async function apiPost<T>(page: Page, path: string, body: unknown): Promise<T> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const r = await page.request.post(path, { data: body, headers: { "X-CSRF-Token": csrf } });
  expect(r.status(), await r.text()).toBeLessThan(300);
  return r.json();
}

async function makeAgents(page: Page, suffix: string) {
  const providers = await (await page.request.get("/api/providers")).json();
  const mock = providers.find((p: { kind: string }) => p.kind === "mock");
  const base = { provider_id: mock.id, model: "mock-model", execution_engine: "api" };
  const pm = await apiPost<{ id: number }>(page, "/api/agents", {
    ...base, name: `Planner ${suffix}`, identifier: `e2e-pm-${suffix}`, role: "project_manager",
    security_profile: "manager", tool_permissions: ["tasks.plan"],
  });
  const dev = await apiPost<{ id: number }>(page, "/api/agents", {
    ...base, name: `Backend ${suffix}`, identifier: `e2e-be-${suffix}`, role: "backend",
  });
  return [`Planner ${suffix}`, `Backend ${suffix}`, pm.id, dev.id] as const;
}

test("objective → planned tasks → agent result → human review → completed (English)", async ({ page }) => {
  const suffix = String(Date.now());
  await login(page, "en");
  const [pmName, devName] = await makeAgents(page, suffix);

  await page.goto("/tasks");
  await page.getByTestId("new-project").click();
  await page.locator("#pr-name").fill(`E2E ${suffix}`);
  await page.getByRole("checkbox", { name: new RegExp(pmName) }).check();
  await page.getByRole("checkbox", { name: new RegExp(devName) }).check();
  await page.getByTestId("project-submit").click();
  await expect(page.locator("#f-project")).toHaveValue(/\d+/);

  await page.getByTestId("new-objective").click();
  await page.locator("#ob-title").fill("Ship the endpoint");
  await page.locator("#ob-text").fill("1. Write endpoint [backend]");
  await page.getByTestId("objective-submit").click();
  await expect(page).toHaveURL(/workflow=\d+/);

  // planned by the (labelled) mock planner, executed by a worker, then waits for a human
  const banner = page.getByTestId("workflow-banner");
  await expect(banner).toContainText("Mock plan — not real AI");
  const review = page.getByTestId("column-REVIEW");
  const card = review.getByRole("button", { name: /Write endpoint/ });
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("column-COMPLETED").getByRole("listitem")).toHaveCount(0);
  await expect(card).toContainText(devName);

  await card.click();
  const details = page.getByTestId("task-details");
  await expect(details.getByTestId("acceptance-criteria")).toContainText("Output addresses: Write endpoint");
  await expect(details.getByTestId("evidence")).toContainText("Execution result");
  await expect(details).toContainText("[MOCK — not real AI]");
  await expect(page).toHaveURL(/task=\d+/); // deep link to the open task

  // a change request needs a reason
  await details.getByRole("button", { name: "Request changes" }).click();
  await expect(details.getByText("Write a note explaining what must change.")).toBeVisible();

  await details.getByTestId("review-approve").click();
  await expect(page.getByRole("status").filter({ hasText: "Approved" }).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("column-COMPLETED").getByRole("button", { name: /Write endpoint/ })).toBeVisible({ timeout: 15_000 });
  await expect(banner).toContainText("Completed", { timeout: 15_000 });

  // list view shows the same task, deep-linked
  await page.getByRole("button", { name: "List" }).click();
  await expect(page).toHaveURL(/view=list/);
  await expect(page.getByTestId("task-table")).toContainText("Approved");
});

for (const lang of ["ar", "en"] as const) {
  test(`axe + no page overflow: /tasks with data (${lang})`, async ({ page }) => {
    await login(page, lang);
    await page.goto("/tasks?view=board");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0); // the board scrolls inside its own region, never the page
    }
    const card = page.locator("[data-testid^=task-card-]").first();
    if (await card.count()) {
      await card.click();
      await expect(page.getByTestId("task-details")).toBeVisible();
    }
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
  });
}
