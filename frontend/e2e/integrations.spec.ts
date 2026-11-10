import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}

for (const lang of ["ar", "en"] as const) {
  test(`skills & MCP: key, register + discover + enable a server, inspect + install a skill, axe (${lang})`, async ({ page }) => {
    const en = lang === "en";
    const suffix = `${lang}-${Date.now() % 1000000}`;
    await login(page, lang);
    // start from a clean slate for host skills so the inspect → install path always runs
    const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
    for (const k of (await (await page.request.get("/api/integrations/skills")).json()) as { id: number; source: string }[]) {
      if (k.source.startsWith("local:")) await page.request.delete(`/api/integrations/skills/${k.id}`, { headers: { "X-CSRF-Token": csrf } });
    }
    await page.goto("/integrations");

    // an access key for the platform's own MCP server, shown once
    await page.getByTestId("tab-keys").click();
    await page.locator("#k-name").fill(`e2e ${suffix}`);
    await page.getByRole("checkbox", { name: "list_agents" }).check();
    await page.getByTestId("key-create").click();
    const token = (await page.getByTestId("key-token").textContent())!.trim();
    expect(token).toMatch(/^acc_mcp_/);
    await axe(page);

    // register the platform's own MCP server as an external server, discover its tools, enable it
    await page.getByTestId("tab-servers").click();
    await page.getByTestId("mcp-add").click();
    await page.locator("#r-name").fill(`self-${suffix}`);
    await page.locator("#r-url").fill("http://127.0.0.1:8000/api/mcp/");
    await page.locator("#r-token").fill(token);
    await page.getByTestId("r-submit").click();
    const card = page.getByTestId(`mcp-self-${suffix}`);
    await expect(card).toBeVisible();
    await card.getByRole("button").click();
    const dlg = page.getByRole("dialog");
    await dlg.getByTestId("mcp-discover").click();
    await expect(dlg.getByTestId("mcp-tools")).toContainText("list_agents");
    await expect(dlg.getByTestId("mcp-tools")).toContainText("send_prompt");
    await dlg.getByTestId("mcp-enable").click();
    const enableDlg = page.getByRole("dialog").last();
    if (await enableDlg.getByTestId("accept-risks").count()) await enableDlg.getByTestId("accept-risks").check();
    await enableDlg.getByTestId("confirm-enable").click();
    await expect(page.getByRole("dialog").first()).toContainText(en ? "Enabled" : "مفعّل");
    await dlg.getByRole("checkbox", { name: /list_agents/ }).click(); // saved, then re-read from the server
    await expect(dlg.getByRole("checkbox", { name: /list_agents/ })).toBeChecked();
    await axe(page);
    await page.keyboard.press("Escape");

    // skills: inspect before install; installed skills start disabled
    await page.goto("/skills"); // Skills has its own navigation item
    const inspect = page.getByRole("button", { name: new RegExp(en ? "^Inspect" : "^فحص") }).first();
    {
      await inspect.click();
      await expect(page.getByRole("dialog")).toContainText(en ? "Dangerous commands" : "أوامر خطرة");
      await axe(page);
      await page.getByTestId("skill-install").click();
      await expect(page.getByText(en ? "Disabled" : "معطّل").first()).toBeVisible();
    }
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    await axe(page);
  });
}
