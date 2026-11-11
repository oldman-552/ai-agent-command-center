import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

// UX-01: the main navigation (the 20 items of the spec plus the AI Workforce, which opens on its Operations Room) — every page loads, has a heading, no
// console errors, no failed API calls.
const PAGES = ["/", "/workforce/room", "/projects", "/agents", "/teams", "/tasks", "/orchestration", "/supervisor", "/executions",
  "/browser-bridge", "/integrations", "/providers", "/runtime", "/memory", "/skills", "/github", "/approvals",
  "/evaluations", "/analytics", "/audit", "/settings"];

for (const lang of ["ar", "en"] as const) {
  test(`all ${PAGES.length} navigation items work (${lang})`, async ({ page }) => {
    test.setTimeout(180_000);
    await login(page, lang);
    const nav = page.getByRole("navigation").first();
    const hrefs = await nav.locator("a[href^='/']").evaluateAll((as) => as.map((a) => a.getAttribute("href")));
    expect(new Set(hrefs)).toEqual(new Set(PAGES));
    for (const path of PAGES) {
      const errors: string[] = [];
      const failed: string[] = [];
      page.on("console", (m) => { if (m.type() === "error") errors.push(`${path}: ${m.text()}`); });
      page.on("response", (r) => { if (r.url().includes("/api/") && r.status() >= 500) failed.push(`${path}: ${r.status()} ${r.url()}`); });
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      const a11y = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      expect(a11y.violations.map((v) => `${path} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 2).join(", ")}`)).toEqual([]);
      expect(failed).toEqual([]);
      expect(errors.filter((e) => !e.includes("favicon"))).toEqual([]);
      page.removeAllListeners("console");
      page.removeAllListeners("response");
    }
  });
}
