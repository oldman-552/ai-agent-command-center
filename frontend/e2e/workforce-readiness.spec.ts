import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

// ADR-037: "is the team ready?" — every missing piece with its fix, before a goal is sent. Here the Browser Bridge
// is off and no bridge process runs: the panel names both, turns the bridge on, takes the terms acknowledgement and
// keeps "Set up the whole team" closed until the bridge runs. Tier: local integration, no site contacted.
test.skip(!PASS, "E2E_PASSWORD not set");

async function send(page: Page, method: "PUT" | "DELETE", path: string, data?: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  return page.request.fetch(path, { method, data, headers: { "X-CSRF-Token": csrf } });
}

for (const lang of ["ar", "en"] as const) {
  test(`the readiness panel names what is missing and fixes it step by step (${lang})`, async ({ page }) => {
    const en = lang === "en";
    await login(page, lang);
    try {
      await page.goto("/workforce/workers");
      const panel = page.getByTestId("wf-readiness");
      await expect(panel).toHaveAttribute("data-ready", "false");
      await expect(panel).toContainText(en ? "The team is not ready yet" : "الفريق غير جاهز بعد");
      await expect(page.getByTestId("wf-readiness-enabled")).toHaveAttribute("data-ok", "false");
      await expect(page.getByTestId("wf-readiness-setup-all")).toBeDisabled();
      // every member without its own window is listed with its fix
      await expect(page.getByTestId("wf-readiness-member-W1")).toBeVisible();

      await page.getByTestId("wf-readiness-enable").click();
      await expect(page.getByTestId("wf-readiness-enabled")).toHaveAttribute("data-ok", "true");
      await expect(page.getByTestId("wf-readiness-statement")).toContainText("Arena");
      await page.getByTestId("wf-readiness-ack").click();
      await expect(page.getByTestId("wf-readiness-terms")).toHaveAttribute("data-ok", "true");
      // no bridge process in this environment: setting up the team stays closed and says why
      if ((await page.getByTestId("wf-readiness-running").getAttribute("data-ok")) === "false") {
        await expect(page.getByTestId("wf-readiness-setup-all")).toBeDisabled();
        await expect(page.getByTestId("wf-readiness-open-W1")).toBeDisabled();
      }

      // the Operations Room shows the same panel, folded
      await page.goto("/workforce/room");
      await expect(page.getByTestId("wf-readiness")).toBeVisible();
      await page.getByTestId("wf-readiness-toggle").click();
      await expect(page.getByTestId("wf-readiness-member-W1")).toBeVisible();
    } finally {
      await send(page, "DELETE", "/api/browser-bridge/providers/arena/acknowledge");
      await send(page, "PUT", "/api/browser-bridge/enabled", { enabled: false });
    }
  });
}
