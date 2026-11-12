import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

// UX-01: the main navigation (the 20 items of the spec plus the AI Workforce) — every page loads, has a heading, no
// console errors, no failed API calls. ADR-036: the menu is simplified by default (daily work + setup); the older
// general-purpose pages appear once "Show advanced tools" is on in Settings.
const SIMPLE = ["/workforce/room", "/workforce/tasks", "/workforce/workers", "/approvals", "/projects", "/",
  "/providers", "/workforce/bridge", "/browser-bridge", "/settings"];
const ADVANCED = ["/agents", "/teams", "/tasks", "/orchestration", "/supervisor", "/executions", "/integrations", "/runtime",
  "/memory", "/skills", "/github", "/evaluations", "/analytics", "/audit"];
const PAGES = [...SIMPLE, ...ADVANCED];

async function navHrefs(page: import("@playwright/test").Page) {
  const nav = page.getByRole("navigation").first();
  return nav.locator("a[href^='/']").evaluateAll((as) => as.map((a) => a.getAttribute("href")?.split("#")[0]));
}

for (const lang of ["ar", "en"] as const) {
  test(`the menu is simplified until advanced tools are switched on (${lang})`, async ({ page }) => {
    await login(page, lang);
    await expect(page).toHaveURL(/\/workforce\/room$/); // signing in opens the Operations Room
    await expect(page.getByTestId("nav-group-advanced")).toHaveCount(0);
    expect(new Set(await navHrefs(page))).toEqual(new Set([...SIMPLE, "/settings"]));
    // an advanced page still opens by its address, and the menu shows its group while it is open
    await page.goto("/agents");
    await expect(page.getByTestId("nav-group-advanced")).toBeVisible();
    await page.goto("/settings");
    await page.getByTestId("settings-show-advanced").check();
    await expect(page.getByTestId("nav-group-advanced")).toBeVisible();
    expect(new Set(await navHrefs(page))).toEqual(new Set(PAGES));
    await page.goto("/orchestration");
    await expect(page.getByTestId("orch-use-workforce")).toBeVisible();
    await page.goto("/settings");
    await page.getByTestId("settings-show-advanced").uncheck();
    await expect(page.getByTestId("nav-group-advanced")).toHaveCount(0);
  });
}

for (const lang of ["ar", "en"] as const) {
  test(`all ${PAGES.length} navigation items work (${lang})`, async ({ page }) => {
    test.setTimeout(180_000);
    await login(page, lang);
    await page.evaluate(() => localStorage.setItem("acc-nav-advanced", "1"));
    await page.reload();
    await expect(page.getByTestId("nav-group-advanced")).toBeVisible();
    expect(new Set(await navHrefs(page))).toEqual(new Set(PAGES));
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
