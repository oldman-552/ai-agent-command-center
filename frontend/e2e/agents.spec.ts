import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

test("create, view, edit, start, archive and delete an agent (Arabic)", async ({ page }) => {
  const name = `E2E Agent ${Date.now()}`;
  await login(page, "ar");
  await page.goto("/agents");
  await page.getByTestId("new-agent").click();
  await page.locator("#name").fill(name);
  await page.locator("#role").fill("backend");
  await page.locator("#security_profile").selectOption("read_only");
  // tools outside the read_only ceiling are disabled in the UI
  await expect(page.locator('input[value="files.write"]')).toBeDisabled();
  await page.locator("#priority").fill("8");
  await page.getByTestId("agent-submit").click();
  await page.waitForURL(/\/agents\/\d+$/);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(name);
  await expect(page.getByTestId("agent-transitions")).toContainText("تم الإنشاء");

  await page.getByRole("link", { name: "تعديل" }).click();
  await page.locator("#description").fill("وصف محدّث");
  await page.getByTestId("agent-submit").click();
  await page.waitForURL(/\/agents\/\d+$/);
  await expect(page.locator("dl bdi", { hasText: "وصف محدّث" })).toBeVisible();

  await page.goto("/agents");
  const row = page.getByRole("row", { name: new RegExp(name) });
  await row.getByRole("button", { name: `تشغيل: ${name}` }).click();
  await expect(row.getByText("جاهز")).toBeVisible();
  await row.getByRole("button", { name: `أرشفة: ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "أرشفة" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);

  await page.getByText("عرض المؤرشفين").click();
  const arow = page.getByRole("row", { name: new RegExp(name) });
  await arow.getByRole("button", { name: `حذف نهائي: ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "حذف نهائي" }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0);
});

test("instantiate an agent from a template (English)", async ({ page }) => {
  await login(page, "en");
  await page.goto("/templates");
  await expect(page.getByTestId("templates-grid").getByRole("listitem").nth(17)).toBeVisible();
  await page.getByRole("button", { name: "Create agent from this template: QA Engineer Agent" }).click();
  await page.waitForURL(/\/agents\/\d+\/edit$/);
  await expect(page.locator("#role")).toHaveValue("qa");
  await expect(page.locator('input[value="shell.test"]')).toBeChecked();
});

for (const lang of ["ar", "en"] as const) {
  for (const path of ["/agents", "/agents/new", "/templates"]) {
    test(`axe: ${path} (${lang})`, async ({ page }) => {
      await login(page, lang);
      await page.goto(path);
      await expect(page.locator("main h1")).toBeVisible();
      await page.waitForLoadState("networkidle");
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
      expect(r.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
}
