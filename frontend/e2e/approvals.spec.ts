import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

async function apiPost<T>(page: Page, path: string, body: unknown): Promise<T> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const r = await page.request.post(path, { data: body, headers: { "X-CSRF-Token": csrf } });
  expect(r.status(), await r.text()).toBeLessThan(300);
  return r.json();
}

/** A project whose plan contains one production deployment, driven up to the approval stage. */
async function criticalTaskAwaitingApproval(page: Page) {
  const s = String(Date.now());
  const providers = await (await page.request.get("/api/providers")).json();
  const mock = providers.find((p: { kind: string }) => p.kind === "mock");
  const base = { provider_id: mock.id, model: "mock-model", execution_engine: "api" };
  const pm = await apiPost<{ id: number }>(page, "/api/agents", { ...base, name: `PM ${s}`, identifier: `ap-pm-${s}`,
    role: "project_manager", security_profile: "manager", tool_permissions: ["tasks.plan"] });
  const ops = await apiPost<{ id: number }>(page, "/api/agents", { ...base, name: `Ops ${s}`, identifier: `ap-ops-${s}`, role: "devops" });
  const proj = await apiPost<{ id: number }>(page, "/api/projects", { name: `Approvals ${s}`, agent_ids: [pm.id, ops.id] });
  const wf = await apiPost<{ id: number }>(page, "/api/workflows", { project_id: proj.id, title: `Release ${s}`,
    objective: "1. Deploy release [devops] {deploy.production}" });
  let taskId = 0;
  await expect.poll(async () => {
    const w = await (await page.request.get(`/api/workflows/${wf.id}`)).json();
    taskId = w.tasks?.[0]?.id ?? 0;
    return w.tasks?.[0]?.status;
  }, { timeout: 30_000 }).toBe("REVIEW");
  return { wfId: wf.id, taskId };
}

test("critical task: evaluation passed → review → approval center decision → completed", async ({ page }) => {
  await login(page, "en");
  const { wfId, taskId } = await criticalTaskAwaitingApproval(page);

  await page.goto(`/tasks?workflow=${wfId}&task=${taskId}`);
  const details = page.getByTestId("task-details");
  await expect(details.getByTestId("evaluation")).toContainText("Passed");
  await expect(details.getByTestId("critical-operation")).toContainText("Production deployment");
  await details.getByTestId("review-approve").click();
  await expect(details.getByText("Approval", { exact: true })).toBeVisible({ timeout: 15_000 }); // status: Approval stage

  // the nav shows a pending count; the approval is critical and needs an explicit acknowledgement
  await expect(page.getByTestId("nav-approvals-count").first()).toBeVisible();
  await details.getByRole("link", { name: "Open approval" }).click();
  const ap = page.getByTestId("approval-details");
  await expect(ap).toContainText("Critical");
  await expect(ap).toContainText("Production deployment");
  await expect(ap.getByTestId("approval-evidence")).toContainText("Payload fingerprint");
  await expect(ap.getByTestId("ap-approve")).toBeDisabled();
  await ap.getByRole("button", { name: "Reject" }).click();
  await expect(ap.getByText("Write the reason for this decision.")).toBeVisible();
  await ap.getByTestId("ap-ack").check();
  await ap.getByTestId("ap-approve").click();
  await expect(page.getByRole("status").filter({ hasText: "Approved" }).first()).toBeVisible();

  await page.goto(`/tasks?workflow=${wfId}`);
  await expect(page.getByTestId("column-COMPLETED").getByRole("button", { name: /Deploy release/ })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("workflow-banner")).toContainText("Completed", { timeout: 15_000 });
});

for (const lang of ["ar", "en"] as const) {
  test(`axe + no page overflow: /approvals with a pending request (${lang})`, async ({ page }) => {
    await login(page, lang);
    const { taskId } = await criticalTaskAwaitingApproval(page);
    await apiPost(page, `/api/tasks/${taskId}/review`, { decision: "approve", note: "" });
    await page.goto("/approvals");
    await expect(page.getByTestId("approval-list")).toBeVisible();
    for (const width of [375, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
    }
    await page.getByTestId("approval-list").getByRole("button").first().click();
    await expect(page.getByTestId("approval-details")).toBeVisible();
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
  });
}
