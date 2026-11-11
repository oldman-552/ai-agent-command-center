import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

// ADR-035: a worker can drive its own visible browser window (a profile of its own, signed in by hand). The real
// window opens on the owner's computer through the Browser Bridge; here (no bridge, Browser Bridge off) the dialog
// offers it and the platform explains why it cannot open yet. Tier: local integration, no site contacted.
test.skip(!PASS, "E2E_PASSWORD not set");

for (const lang of ["ar", "en"] as const) {
  test(`worker dialog offers the worker's own window and explains a refusal (${lang})`, async ({ page }) => {
    await login(page, lang);
    await page.goto("/workforce/workers");
    await page.getByTestId("wf-worker-edit-1").click();
    const section = page.getByTestId("wf-worker-browser");
    await expect(section).toBeVisible();
    await expect(section).toContainText(lang === "ar" ? "نافذة خاصة بهذا العامل" : "This worker's own window");
    await page.getByTestId("wf-worker-open-browser").click();
    // the Browser Bridge is off in this environment: the gate answers before anything opens
    await expect(page.getByRole("status").or(page.getByRole("alert")).filter({ hasText: /bridge_disabled|جسر المتصفح|Browser Bridge/ }).first())
      .toBeVisible();
  });
}
