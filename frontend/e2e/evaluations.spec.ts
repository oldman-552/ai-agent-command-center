import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

for (const lang of ["ar", "en"] as const) {
  test(`evaluations: profiles by group, recent verdicts, axe (${lang})`, async ({ page }) => {
    await login(page, lang);
    await page.goto("/evaluations");
    // earlier E2E workflows produced evaluations through the real evaluator
    await expect(page.getByTestId("profiles-table")).toBeVisible();
    await expect(page.getByTestId("recent-evaluations").getByRole("listitem").first()).toBeVisible();
    await page.locator("#e-group").selectOption("task_type");
    await expect(page).toHaveURL(/group=task_type/);
    await expect(page.getByTestId("profiles-table")).toBeVisible();
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
  });
}
