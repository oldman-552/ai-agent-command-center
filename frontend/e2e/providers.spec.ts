import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

test("add provider with write-only key, price a model, test mock, delete (English)", async ({ page }) => {
  const name = `Compat ${Date.now()}`;
  const key = "sk-e2e-secret-key-ABCD";
  await login(page, "en");
  await page.goto("/providers");
  // built-in mock provider: connection test succeeds and is clearly labelled
  const mock = page.locator("[data-testid^=provider-]", { hasText: "Test only — not real AI" });
  await mock.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("status")).toContainText("Connection succeeded.");

  await page.getByTestId("add-provider").click();
  await page.locator("#p-kind").selectOption("openai_compatible");
  await page.locator("#p-name").fill(name);
  await page.locator("#p-url").fill("http://127.0.0.1:9/v1");
  await page.locator("#p-key").fill(key);
  await page.getByTestId("provider-submit").click();
  const card = page.locator("[data-testid^=provider-]", { hasText: name });
  await expect(card).toContainText("Saved (…ABCD)");
  expect(await page.content()).not.toContain(key); // key is never sent back to the browser

  await card.getByRole("button", { name: "Add model" }).click();
  await page.locator("#m-name").fill("local-model");
  await page.locator("#m-in").fill("1.5");
  await page.locator("#m-out").fill("6");
  await page.getByTestId("model-submit").click();
  await expect(card.getByRole("row", { name: /local-model/ })).toContainText("$1.5");

  // nothing listens on 127.0.0.1:9 — the failure is reported, not hidden
  await card.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("status")).toContainText("Connection failed");

  await card.getByRole("button", { name: `Delete provider: ${name}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Delete provider" }).click();
  await expect(page.locator("[data-testid^=provider-]", { hasText: name })).toHaveCount(0);
});

for (const lang of ["ar", "en"] as const) {
  test(`axe: /providers (${lang})`, async ({ page }) => {
    await login(page, lang);
    await page.goto("/providers");
    await expect(page.locator("[data-testid^=provider-]").first()).toBeVisible();
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  });
}
