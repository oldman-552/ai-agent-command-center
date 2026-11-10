import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

/**
 * FI-05 — the spec's Definition of Done as one scenario with the (labelled) Mock provider:
 * sign in → dashboard → agents + project + verified memory → objective decomposed into tasks with dependencies →
 * two tasks run in parallel → reviews → dependent tasks follow → a critical task goes through the Approval Center →
 * everything completed with evidence → execution timeline, context preview, audit log, analytics — in Arabic (RTL)
 * and with axe checks along the way.
 */
async function api<T>(page: Page, method: "POST" | "PUT", path: string, body: unknown): Promise<T> {
  const csrf = (await page.context().cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const r = await page.request.fetch(path, { method, data: body, headers: { "X-CSRF-Token": csrf } });
  expect(r.status(), `${path}: ${await r.text()}`).toBeLessThan(300);
  return r.json();
}
async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`)).toEqual([]);
}
type Wf = { status: string; tasks: { id: number; title: string; status: string; assigned_agent_id: number | null; started_at: string | null; finished_at: string | null }[] };
const getWf = async (page: Page, id: number) => (await (await page.request.get(`/api/workflows/${id}`)).json()) as Wf;
const byTitle = (w: Wf, t: string) => w.tasks.find((x) => x.title.includes(t))!;

test("Definition of Done: objective to completed, critical approval, evidence, RTL", async ({ page }) => {
  test.setTimeout(240_000);
  const s = String(Date.now() % 10_000_000);
  await login(page, "ar");

  // core platform: authenticated, dashboard shows real data
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");

  // agents + project
  const providers = await (await page.request.get("/api/providers")).json();
  const mock = providers.find((p: { kind: string }) => p.kind === "mock");
  const base = { provider_id: mock.id, model: "mock-model", execution_engine: "api" };
  const pm = await api<{ id: number }>(page, "POST", "/api/agents", { ...base, name: `DoD PM ${s}`, identifier: `dod-pm-${s}`,
    role: "project_manager", security_profile: "manager", tool_permissions: ["tasks.plan"] });
  const be = await api<{ id: number }>(page, "POST", "/api/agents", { ...base, name: `DoD Backend ${s}`, identifier: `dod-be-${s}`, role: "backend" });
  const fe = await api<{ id: number }>(page, "POST", "/api/agents", { ...base, name: `DoD Frontend ${s}`, identifier: `dod-fe-${s}`, role: "frontend" });
  const ops = await api<{ id: number }>(page, "POST", "/api/agents", { ...base, name: `DoD Ops ${s}`, identifier: `dod-ops-${s}`, role: "devops" });
  const proj = await api<{ id: number }>(page, "POST", "/api/projects", { name: `DoD ${s}`, agent_ids: [pm.id, be.id, fe.id, ops.id] });
  // intelligence: a verified project memory the agents will receive as (untrusted, fenced) context
  await api(page, "POST", "/api/memory", { memory_type: "technical_decision", project_id: proj.id, title: "API style",
    content: "All endpoints return JSON with snake_case keys." });

  // orchestration: decomposition with dependencies and one critical operation
  const wf = await api<{ id: number }>(page, "POST", "/api/workflows", { project_id: proj.id, title: `Release ${s}`,
    objective: "1. Build API [backend]\n2. Build UI [frontend]\n3. Integrate API and UI [backend] (after 1,2)\n" +
      "4. Deploy release [devops] (after 3) {deploy.production}" });
  await expect.poll(async () => (await getWf(page, wf.id)).tasks.length, { timeout: 30_000 }).toBe(4);

  // parallel execution: both independent tasks reach review while the dependent ones wait
  await expect.poll(async () => {
    const w = await getWf(page, wf.id);
    const waiting = ["PLANNED", "BACKLOG"].includes(byTitle(w, "Integrate").status) ? "waiting" : byTitle(w, "Integrate").status;
    return [byTitle(w, "Build API").status, byTitle(w, "Build UI").status, waiting];
  }, { timeout: 60_000 }).toEqual(["REVIEW", "REVIEW", "waiting"]);
  let w = await getWf(page, wf.id);
  expect(byTitle(w, "Build API").assigned_agent_id).toBe(be.id);
  expect(byTitle(w, "Build UI").assigned_agent_id).toBe(fe.id); // assigned by role/skill

  // the board in Arabic, reviewed by a human through the UI
  await page.goto(`/tasks?project=${proj.id}&workflow=${wf.id}`);
  await axe(page);
  for (const t of ["Build API", "Build UI"]) {
    await page.getByTestId("column-REVIEW").getByRole("button", { name: new RegExp(t) }).click();
    const details = page.getByTestId("task-details");
    await expect(details.getByTestId("evidence")).toBeVisible();
    await details.getByTestId("review-approve").click();
    await page.keyboard.press("Escape");
  }
  // dependency satisfied → the integration task runs; approve it via the API to keep the scenario short
  await expect.poll(async () => byTitle(await getWf(page, wf.id), "Integrate").status, { timeout: 60_000 }).toBe("REVIEW");
  w = await getWf(page, wf.id);
  await api(page, "POST", `/api/tasks/${byTitle(w, "Integrate").id}/review`, { decision: "approve", note: "" });

  // critical operation: review, then the Approval Center (human-only, frozen payload)
  await expect.poll(async () => byTitle(await getWf(page, wf.id), "Deploy").status, { timeout: 60_000 }).toBe("REVIEW");
  w = await getWf(page, wf.id);
  const deploy = byTitle(w, "Deploy");
  await api(page, "POST", `/api/tasks/${deploy.id}/review`, { decision: "approve", note: "" });
  await expect.poll(async () => byTitle(await getWf(page, wf.id), "Deploy").status, { timeout: 30_000 }).toBe("APPROVAL");
  await page.goto("/approvals");
  await page.getByTestId("approval-list").getByRole("button", { name: new RegExp(`Deploy release`) }).first().click();
  const ap = page.getByTestId("approval-details");
  await expect(ap.getByTestId("ap-approve")).toBeDisabled();
  await ap.getByTestId("ap-ack").check();
  await axe(page);
  await ap.getByTestId("ap-approve").click();

  // results consolidated: every task completed, the workflow completed
  await expect.poll(async () => (await getWf(page, wf.id)).status, { timeout: 60_000 }).toBe("completed");
  w = await getWf(page, wf.id);
  expect(w.tasks.map((t) => t.status).sort()).toEqual(["COMPLETED", "COMPLETED", "COMPLETED", "COMPLETED"]);
  // dependencies respected: integration started only after both builds finished
  const ended = Math.max(...["Build API", "Build UI"].map((t) => Date.parse(byTitle(w, t).finished_at ?? "")));
  expect(Date.parse(byTitle(w, "Integrate").started_at ?? "")).toBeGreaterThanOrEqual(ended - 1000);

  // evidence everywhere: execution timeline, context with the memory fenced as data, audit, analytics
  const task = await (await page.request.get(`/api/tasks/${byTitle(w, "Integrate").id}`)).json();
  const exId = task.executions[0].id as string;
  await page.goto(`/executions?id=${exId}`);
  await expect(page.getByTestId("ex-timeline")).toContainText("COMPLETED");
  const ctx = await (await page.request.get(`/api/context/tasks/${byTitle(w, "Integrate").id}`)).json();
  expect(ctx.prompt).toContain("UNTRUSTED DATA");
  expect(ctx.prompt).toContain("snake_case");
  expect(ctx.system).not.toContain("snake_case");
  const audit = await (await page.request.get(`/api/audit?action=approvals.approved&limit=5`)).json();
  expect(JSON.stringify(audit)).toContain("approvals.approved");
  await page.goto("/analytics");
  await expect(page.getByTestId("an-totals")).toBeVisible();
  await page.goto("/audit");
  await axe(page);
});
