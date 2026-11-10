import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

for (const lang of ["ar", "en"] as const) {
  test(`projects, teams, automations, executions timeline, analytics & budgets (${lang})`, async ({ page }) => {
    test.setTimeout(180_000);
    const en = lang === "en";
    const suffix = `${lang}-${Date.now() % 1000000}`;
    await login(page, lang);

    // projects: create, then switch to maintenance mode
    await page.goto("/projects");
    await page.getByTestId("project-new").click();
    await page.locator("#p-name").fill(`E2E project ${suffix}`);
    await page.locator("#p-mode").selectOption("existing");
    await page.getByTestId("project-save").click();
    const card = page.locator("li", { hasText: `E2E project ${suffix}` });
    await expect(card).toContainText(en ? "Existing" : "قائم");
    await card.getByRole("button", { name: new RegExp(en ? "^Edit" : "^تعديل") }).click();
    await page.locator("#p-mode").selectOption("maintenance");
    await page.getByTestId("project-save").click();
    await expect(card).toContainText(en ? "Maintenance" : "صيانة");
    await card.getByRole("button", { name: new RegExp(en ? "^Repository map" : "^خريطة المستودع") }).click();
    await expect(page.getByRole("dialog")).toContainText(en ? "Not analyzed yet." : "لم يُحلَّل بعد.");
    await axe(page);
    await page.keyboard.press("Escape");

    // teams: create with two agents and a lead
    await page.goto("/teams");
    await page.getByTestId("team-new").click();
    await page.locator("#t-name").fill(`E2E team ${suffix}`);
    const boxes = page.getByRole("dialog").locator("fieldset").first().getByRole("checkbox");
    await boxes.nth(0).check();
    await boxes.nth(1).check();
    await page.locator("#t-lead").selectOption({ index: 1 });
    await page.getByTestId("team-save").click();
    const team = page.locator("li", { hasText: `E2E team ${suffix}` });
    await expect(team).toContainText(en ? "2 agents" : "2 وكلاء");
    await expect(team.getByLabel(en ? "Lead agent" : "الوكيل القائد")).toBeVisible();
    await axe(page);

    // automations: create, pause, resume, run now, history, delete
    await page.goto("/orchestration");
    await page.getByTestId("otab-automations").click();
    await page.getByTestId("auto-new").click();
    await page.locator("#au-name").fill(`E2E nightly ${suffix}`);
    await page.locator("#au-project").selectOption({ label: `E2E project ${suffix}` });
    await page.locator("#au-expr").fill("0 3 * * 1-5");
    await page.locator("#au-title").fill("Nightly review");
    await page.locator("#au-obj").fill("Review yesterday's changes");
    await page.getByTestId("auto-save").click();
    const auto = page.locator("li", { hasText: `E2E nightly ${suffix}` });
    await expect(auto.getByTestId("auto-status")).toHaveText(en ? "Active" : "نشطة");
    await auto.getByRole("button", { name: new RegExp(en ? "^Pause" : "^إيقاف مؤقت") }).click();
    await expect(auto.getByTestId("auto-status")).toHaveText(en ? "Paused" : "متوقفة مؤقتًا");
    await auto.getByRole("button", { name: new RegExp(en ? "^Resume" : "^استئناف") }).click();
    await expect(auto.getByTestId("auto-status")).toHaveText(en ? "Active" : "نشطة");
    await auto.getByRole("button", { name: new RegExp(en ? "^Run now" : "^تشغيل الآن") }).click();
    await auto.getByRole("button", { name: new RegExp(en ? "^History" : "^السجل") }).click();
    await expect(page.getByTestId("auto-runs")).toContainText(en ? "Succeeded" : "نجح");
    await axe(page);
    await page.keyboard.press("Escape");
    await auto.getByRole("button", { name: new RegExp(en ? "^Delete" : "^حذف") }).click();
    await page.getByRole("dialog").getByRole("button", { name: en ? "Delete" : "حذف" }).click();
    await expect(page.locator("li", { hasText: `E2E nightly ${suffix}` })).toHaveCount(0);
    // the automation submitted a real objective
    await page.getByTestId("otab-workflows").click();
    await expect(page.getByTestId("wf-list")).toContainText("Nightly review");

    // executions: pick the newest and read its timeline (UX-03)
    await page.goto("/executions");
    const first = page.getByTestId("ex-list").locator("button").first();
    if (await first.count()) {
      await first.click();
      const tl = page.getByTestId("ex-timeline");
      await expect(tl.getByRole("list", { name: en ? "Timeline" : "الخط الزمني" }).locator("li").first()).toBeVisible();
      await expect(tl).toContainText(en ? "State" : "الحالة");
      await axe(page);
    }

    // analytics & budgets
    await page.goto("/analytics");
    await expect(page.getByTestId("an-totals")).toBeVisible();
    await page.locator("#an-group").selectOption("agent");
    await expect(page.getByTestId("an-table")).toBeVisible();
    await page.locator("#b-scope").selectOption("global");
    await page.locator("#b-period").selectOption("daily");
    await page.locator("#b-tok").fill("999999999");
    await page.getByTestId("budget-add").click();
    await expect(page.getByTestId("budget-list")).toContainText(en ? "Organization" : "المؤسسة");
    await axe(page);
    const del = page.getByTestId("budget-list").getByRole("button").last();
    await del.click(); // leave no global budget behind
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }

    // settings: add a user
    await page.goto("/settings");
    await page.locator("#u-name").fill(`e2e_${suffix.replace("-", "_")}`);
    await page.locator("#u-pass").fill("e2e-user-password-123");
    await page.locator("#u-role").selectOption("VIEWER");
    await page.getByTestId("user-add").click();
    await expect(page.getByTestId("user-table")).toContainText(`e2e_${suffix.replace("-", "_")}`);
    await axe(page);
  });
}
