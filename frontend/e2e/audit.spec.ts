import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

test("audit log: filter by severity, inspect details, export (English)", async ({ page }) => {
  await login(page, "en");
  await page.goto("/audit");
  await expect(page.getByTestId("audit-table")).toBeVisible();
  // sign-in itself was audited
  await page.locator("#a-q").fill("auth.login");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/q=auth\.login/);
  await expect(page.getByTestId("audit-table")).toContainText("auth.login");
  await page.getByTestId("audit-table").getByRole("button", { name: /auth\.login/ }).first().click();
  await expect(page.getByRole("dialog")).toContainText("Details");
  await page.keyboard.press("Escape");
  await page.locator("#a-q").fill("");
  await page.getByRole("button", { name: "Search" }).click();
  await page.locator("#a-level").selectOption("warning");
  await expect(page).toHaveURL(/level=warning/);
  const href = await page.getByTestId("audit-export").getAttribute("href");
  expect(href).toContain("level=warning");
  const csv = await page.request.get(href!);
  expect(csv.status()).toBe(200);
  expect(await csv.text()).toContain("id,ts,actor,action");
});

for (const lang of ["ar", "en"] as const) {
  test(`axe + no page overflow: /audit (${lang})`, async ({ page }) => {
    await login(page, lang);
    await page.goto("/audit");
    await expect(page.getByTestId("audit-table")).toBeVisible();
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    await page.getByTestId("audit-table").getByRole("button").first().click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
  });
}
