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
    await expect(page.getByTestId("bb-provider-arena")).toContainText(en ? "Locked" : "مقفل");
    await axe(page);

    // the terms gate: the confirm button stays disabled until the box is ticked
    await page.getByTestId("bb-provider-arena").getByRole("button", { name: en ? /^Acknowledge/ : /إقرار|أقر/ }).click();
    const confirm = page.getByTestId("bb-ack-confirm");
    await expect(confirm).toBeDisabled();
    await page.getByTestId("bb-ack-check").check();
    await axe(page);
    await confirm.click();
    await expect(page.getByTestId("bb-provider-arena")).toContainText(en ? "Unlocked" : "مفتوح");
    await page.getByTestId("bb-provider-arena").getByRole("button", { name: en ? /^Revoke/ : /سحب|إلغاء/ }).click(); // revoke again
    await expect(page.getByTestId("bb-provider-arena")).toContainText(en ? "Locked" : "مقفل");

    // enable and drive the local test page through a real headless browser
    await page.getByTestId("bb-toggle").click();
    await expect(page.getByTestId("bb-status")).toContainText(en ? "Enabled" : "مفعّل");
    await expect(page.getByTestId("bb-status")).toContainText(en ? "Running" : "تعمل");
    await page.locator("#bb-provider").selectOption("test");
    // a dedicated profile for this run (one window per profile); "My profile" is the default otherwise
    const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
    const prof = `e2e-${lang}-${Date.now() % 100000}`;
    expect((await page.request.post("/api/browser-bridge/profiles", { data: { name: prof, label: prof },
      headers: { "X-CSRF-Token": csrf } })).status()).toBe(201);
    await page.reload();
    await page.locator("#bb-provider").selectOption("test");
    await page.locator("#bb-profile").selectOption(prof);
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

test("arena.ai: its own terms clauses, and the Super Admin can adjust selectors (English)", async ({ page }) => {
  await login(page, "en");
  await page.goto("/browser-bridge");
  const card = page.getByTestId("bb-provider-arena");
  await expect(card).toContainText("Arena agent");
  await card.getByRole("button", { name: /Acknowledge/ }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toContainText("section 5(vi)");
  await expect(dlg).toContainText("section 3.4");
  await page.keyboard.press("Escape");
  await card.getByTestId("bb-sel-arena").click();
  const sel = page.getByTestId("bb-sel-dialog");
  await expect(sel.locator("#sel-input")).toHaveAttribute("placeholder", "textarea");
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(axe.violations.map((v) => v.id)).toEqual([]);
  await sel.locator("#sel-input").fill("div[contenteditable='true']");
  await sel.getByTestId("bb-sel-save").click();
  await expect(card).toContainText("custom selectors");
  const ov = await (await page.request.get("/api/browser-bridge")).json();
  expect(ov.providers.find((p: { key: string }) => p.key === "arena").selectors.input).toBe("div[contenteditable='true']");
  await card.getByTestId("bb-sel-arena").click();
  await page.getByTestId("bb-sel-reset").click();
  await expect(card).not.toContainText("custom selectors");
});

test("profiles per employee and adding any website (English)", async ({ page }) => {
  await login(page, "en");
  await page.goto("/browser-bridge");
  const n = `emp-${Date.now() % 100000}`;
  const prof = page.getByTestId("bb-profiles");
  await prof.locator("#pf-name").fill(n);
  await prof.locator("#pf-label").fill("Employee one");
  await prof.locator("#pf-owner").selectOption({ label: "admin" });
  await prof.getByTestId("pf-add").click();
  await expect(page.getByTestId(`pf-${n}`)).toContainText("Employee one");
  await expect(page.getByTestId(`pf-${n}`)).toContainText("admin");
  await axe(page);
  await page.getByTestId("bb-add-site").click();
  const dlg = page.getByTestId("bb-site-dialog");
  const key = `site${Date.now() % 100000}`;
  await dlg.locator("#site-name").fill("My chat site");
  await dlg.locator("#site-key").fill(key);
  await dlg.locator("#site-url").fill("https://chat.example.com/app");
  await dlg.locator("#site-terms_url").fill("https://chat.example.com/terms");
  await dlg.getByTestId("bb-site-save").click();
  const card = page.getByTestId(`bb-provider-${key}`);
  await expect(card).toContainText("My chat site");
  await expect(card).toContainText("Locked");
  await card.getByTestId(`bb-remove-${key}`).click();
  await expect(card).toHaveCount(0);
  await page.getByTestId(`pf-${n}`).getByRole("button", { name: /Delete/ }).click();
  await expect(page.getByTestId(`pf-${n}`)).toHaveCount(0);
});
