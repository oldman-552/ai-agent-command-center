import { expect, type Page } from "@playwright/test";

export const USER = process.env.E2E_USERNAME ?? "admin";
export const PASS = process.env.E2E_PASSWORD ?? "";

export async function login(page: Page, lang: "ar" | "en" = "ar") {
  await page.context().clearCookies();
  await page.goto("/login");
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: lang, url: page.url() }]);
  await page.locator("#username").fill(USER);
  await page.locator("#password").fill(PASS);
  await page.locator("button[type=submit]").click();
  await page.waitForURL("/");
  // the login restores the user's saved language; force the one under test
  await page.context().addCookies([{ name: "NEXT_LOCALE", value: lang, url: page.url() }]);
  await expect(page.getByTestId("current-user")).toBeVisible();
}
