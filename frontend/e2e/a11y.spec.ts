import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const USER = process.env.E2E_USERNAME ?? "admin";
const PASS = process.env.E2E_PASSWORD ?? "";

async function setup(page: Page, lang: "ar" | "en", theme: "light" | "dark") {
  await page.context().clearCookies();
  await page.addInitScript((t) => { try { localStorage.setItem("theme", t); } catch {} }, theme);
  await page.goto("/login");
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: lang, url: page.url() }]);
}

async function audit(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  const summary = r.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(", ")}`);
  expect(summary, summary.join("\n")).toEqual([]);
}

for (const lang of ["ar", "en"] as const) {
  for (const theme of ["light", "dark"] as const) {
    test(`axe: login page (${lang}, ${theme})`, async ({ page }) => {
      await setup(page, lang, theme);
      await page.reload();
      await expect(page.locator("html")).toHaveAttribute("lang", lang);
      await audit(page);
      // error state too
      await page.locator("button[type=submit]").click();
      await expect(page.locator("#username-err")).toBeVisible();
      await audit(page);
    });

    test(`axe: dashboard (${lang}, ${theme})`, async ({ page }) => {
      test.skip(!PASS, "E2E_PASSWORD not set");
      await setup(page, lang, theme);
      await page.locator("#username").fill(USER);
      await page.locator("#password").fill(PASS);
      await page.locator("button[type=submit]").click();
      await page.waitForURL("/workforce/room"); // ADR-036: signing in opens the Operations Room
      await page.context().addCookies([{ name: "NEXT_LOCALE", value: lang, url: page.url() }]);
      await page.goto("/");
      await expect(page.locator("[data-testid=recent-activity]")).toBeVisible();
      await audit(page);
    });
  }
}
