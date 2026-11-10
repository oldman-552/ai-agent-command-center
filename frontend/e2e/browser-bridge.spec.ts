import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

async function setEnabled(page: Page, on: boolean) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const r = await page.request.put("/api/browser-bridge/enabled", { data: { enabled: on }, headers: { "X-CSRF-Token": csrf } });
  expect(r.ok()).toBeTruthy();
}

for (const lang of ["ar", "en"] as const) {
  test(`browser bridge: disabled by default, terms gate, live session on the local test page, axe (${lang})`, async ({ page }) => {
    const en = lang === "en";
    await login(page, lang);
    await setEnabled(page, false);
    await page.goto("/browser-bridge");
    await expect(page.getByTestId("bb-status")).toContainText(en ? "Disabled" : "معطّل");
    await expect(page.getByTestId("bb-provider-claude")).toContainText(en ? "Locked" : "مقفل");
    await axe(page);

    // the terms gate: the confirm button stays disabled until the box is ticked
    await page.getByTestId("bb-provider-claude").getByRole("button").click();
    const confirm = page.getByTestId("bb-ack-confirm");
    await expect(confirm).toBeDisabled();
    await page.getByTestId("bb-ack-check").check();
    await axe(page);
    await confirm.click();
    await expect(page.getByTestId("bb-provider-claude")).toContainText(en ? "Unlocked" : "مفتوح");
    await page.getByTestId("bb-provider-claude").getByRole("button").click(); // revoke again
    await expect(page.getByTestId("bb-provider-claude")).toContainText(en ? "Locked" : "مقفل");

    // enable and drive the local test page through a real headless browser
    await page.getByTestId("bb-toggle").click();
    await expect(page.getByTestId("bb-status")).toContainText(en ? "Enabled" : "مفعّل");
    await expect(page.getByTestId("bb-status")).toContainText(en ? "Running" : "تعمل");
    await page.locator("#bb-provider").selectOption("test");
    await page.locator("#bb-profile").fill(`e2e-${lang}-${Date.now() % 100000}`);
    await page.getByTestId("bb-start").click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByTestId("bb-state")).toHaveText(en ? "Ready" : "جاهز", { timeout: 30_000 });
    await dialog.locator("#bb-prompt").fill("hello from the e2e test");
    await dialog.getByTestId("bb-send").click();
    await expect(dialog.getByTestId("bb-history")).toContainText("Echo: hello from the e2e test", { timeout: 30_000 });
    await expect(dialog.getByTestId("bb-state")).toHaveText(en ? "Completed" : "مكتمل");
    await axe(page);
    await dialog.getByTestId("bb-stop").click();
    await expect(dialog.getByTestId("bb-state")).toHaveText(en ? "Stopped" : "متوقف");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("bb-sessions")).toBeVisible();
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    await axe(page);
    await setEnabled(page, false); // leave the bridge disabled, as it ships
  });
}
