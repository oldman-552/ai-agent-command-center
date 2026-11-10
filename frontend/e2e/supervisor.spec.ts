import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

for (const lang of ["ar", "en"] as const) {
  test(`supervisor: health, manual sweep, findings and log; axe (${lang})`, async ({ page }) => {
    await login(page, lang);
    await page.goto("/supervisor");
    await expect(page.getByTestId("sv-health")).toBeVisible();
    await page.getByTestId("sv-run").click();
    await expect(page.getByRole("status").first()).toBeVisible();
    // earlier E2E runs leave approvals and tasks behind; whatever the sweep finds is listed or the all-clear shows
    await expect(page.getByTestId("sv-findings").or(page.getByText(lang === "en" ? "Nothing needs attention." : "لا شيء يحتاج انتباهاً."))).toBeVisible();
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
  });
}
