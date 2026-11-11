import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

// Regression: a bridge whose health reading holds an error (unreachable address, refused key …) made the
// Arena Local Bridge page crash ("This page couldn't load"): the API reports the error as an object and the page
// rendered it as text. Tier: local integration — a real HTTP attempt to an address where nothing listens.
test.skip(!PASS, "E2E_PASSWORD not set");

const W = "/api/workforce";

async function send(page: Page, method: "POST" | "DELETE", path: string, data?: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  return page.request.fetch(path, { method, data, headers: { "X-CSRF-Token": csrf } });
}

for (const lang of ["ar", "en"] as const) {
  test(`bridge page shows a failed health reading instead of crashing (${lang})`, async ({ page }) => {
    await login(page, lang);
    const created = await send(page, "POST", `${W}/bridges`, {
      name: `Unreachable ${lang}`, base_url: "http://127.0.0.1:9", api_key: "0123456789abcdef0123456789abcdef",
    });
    expect(created.status()).toBe(201);
    const b = await created.json();
    try {
      const h = await send(page, "POST", `${W}/bridges/${b.id}/health`);
      expect(h.ok()).toBeTruthy();
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await page.goto("/workforce/bridge");
      await expect(page.getByTestId(`wf-bridge-card-${b.id}`)).toBeVisible();
      await expect(page.getByTestId(`wf-bridge-error-${b.id}`)).toBeVisible();
      await expect(page.getByTestId(`wf-bridge-error-${b.id}`)).not.toContainText("[object Object]");
      expect(errors).toEqual([]);
    } finally {
      await send(page, "DELETE", `${W}/bridges/${b.id}`);
    }
  });
}
