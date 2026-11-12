import { expect, test, type Page } from "@playwright/test";

const USER = process.env.E2E_USERNAME ?? "admin";
const PASS = process.env.E2E_PASSWORD ?? "";
const SHOTS = process.env.SCREENSHOT_DIR;

async function login(page: Page) {
  await page.goto("/login");
  await page.locator("#username").fill(USER);
  await page.locator("#password").fill(PASS);
  await page.locator("button[type=submit]").click();
  await page.waitForURL("/workforce/room"); // ADR-036: signing in opens the Operations Room
  await page.goto("/");
}

test.beforeEach(async ({ context }) => {
  await context.clearCookies();
});

test("Arabic is the default language with RTL layout", async ({ page }) => {
  await page.goto("/login");
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("مركز قيادة وكلاء الذكاء الاصطناعي");
});

test("language switch to English gives LTR and persists across reloads", async ({ page }) => {
  await page.goto("/login");
  await page.getByTestId("language-switcher").click();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("AI Agent Command Center");
});

test("protected pages redirect to login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});

test("wrong password shows translated error", async ({ page }) => {
  await page.goto("/login");
  await page.locator("#username").fill(USER);
  await page.locator("#password").fill("definitely-wrong-password");
  await page.locator("button[type=submit]").click();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toContainText("غير صحيحة");
});

test("empty form shows inline validation", async ({ page }) => {
  await page.goto("/login");
  await page.locator("button[type=submit]").click();
  await expect(page.locator("#username-err")).toBeVisible();
  await expect(page.locator("#password")).toHaveAttribute("aria-invalid", "true");
});

test("login, dashboard with live data in both languages, sidebar side follows direction", async ({ page }) => {
  test.skip(!PASS, "E2E_PASSWORD not set");
  await login(page);
  await expect(page.getByTestId("current-user")).toContainText(USER);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("لوحة التحكم");
  await expect(page.getByTestId("recent-activity")).toContainText("تسجيل دخول");

  await page.setViewportSize({ width: 1440, height: 900 });
  const aside = page.locator("aside").first();
  const rtlBox = await aside.boundingBox();
  expect(rtlBox!.x).toBeGreaterThan(700); // sidebar on the right in Arabic

  await page.getByTestId("language-switcher").click();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Dashboard");
  const ltrBox = await aside.boundingBox();
  expect(ltrBox!.x).toBeLessThan(50); // sidebar on the left in English

  // language saved on the profile: a fresh login restores English
  await page.context().clearCookies();
  await login(page);
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.getByTestId("language-switcher").click(); // restore Arabic
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
});

for (const width of [375, 768, 1024, 1440]) {
  for (const lang of ["ar", "en"] as const) {
    test(`no horizontal overflow at ${width}px (${lang})`, async ({ page, context }) => {
      test.skip(!PASS, "E2E_PASSWORD not set");
      await page.setViewportSize({ width, height: 900 });
      await login(page);
      await context.addCookies([{ name: "NEXT_LOCALE", value: lang, url: page.url() }]);
      await page.reload();
      await expect(page.locator("html")).toHaveAttribute("lang", lang);
      await expect(page.getByText(lang === "ar" ? "إجمالي الوكلاء" : "Total agents")).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow).toBeLessThanOrEqual(0);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/dashboard-${lang}-${width}.png`, fullPage: true });
    });
  }
}
