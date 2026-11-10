import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

async function apiPost(page: Page, path: string, body: unknown) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  return page.request.post(path, { data: body, headers: { "X-CSRF-Token": csrf } });
}

test("connect dialog validates input and reports an unreachable GitHub honestly (English)", async ({ page }) => {
  await login(page, "en");
  await page.goto("/github");
  await page.getByTestId("connect-repo").click();
  const dialog = page.getByRole("dialog");
  await dialog.locator("#gh-name").fill("not a repo");
  await dialog.locator("#gh-token").fill("github_pat_" + "x".repeat(30));
  await dialog.getByTestId("connect-submit").click();
  await expect(dialog.locator("#gh-name")).toHaveJSProperty("validity.valid", false); // native validation, nothing sent
  await dialog.locator("#gh-name").fill(`acme/e2e-${Date.now()}`);
  await dialog.getByTestId("connect-submit").click();
  // this environment cannot reach github.com: the verification failure is shown, nothing is stored
  await expect(dialog.getByRole("alert")).toBeVisible({ timeout: 40_000 });
  await page.keyboard.press("Escape");
  const repos = await (await page.request.get("/api/repositories")).json();
  expect(repos.some((r: { full_name: string }) => r.full_name.startsWith("acme/e2e-"))).toBe(false);
});

for (const lang of ["ar", "en"] as const) {
  test(`axe + no page overflow: /github with a repository (${lang})`, async ({ page }) => {
    await login(page, lang);
    // seeded without verification (the API allows verify=false for an admin; the UI always verifies)
    await apiPost(page, "/api/repositories", { full_name: `acme/seed-${lang}-${Date.now()}`, token: "github_pat_" + "y".repeat(30),
      verify: false, protected_branches: ["main", "release"] });
    await page.goto("/github");
    await expect(page.getByTestId("repo-list").getByRole("listitem").first()).toBeVisible();
    await expect(page.getByTestId("repo-list")).toContainText("release");
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    await page.getByTestId("connect-repo").click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
  });
}
