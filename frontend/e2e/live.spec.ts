import { expect, test } from "@playwright/test";
import { login, PASS } from "./helpers";

test.skip(!PASS, "E2E_PASSWORD not set");

// UX-02: a change made elsewhere appears without reloading (the executions list does not poll).
test("live updates over WebSocket refresh open views", async ({ page, context }) => {
  await login(page, "en");
  await page.goto("/executions");
  await expect(page.getByTestId("live-state")).toHaveAttribute("data-state", "live", { timeout: 15_000 });
  const before = await page.getByTestId("ex-list").locator("li").count();

  // another "user" (a second tab) starts an execution through the API
  const other = await context.newPage();
  await other.goto("/login"); // same session cookies
  const csrf = (await context.cookies()).find((c) => c.name === "acc_csrf")?.value ?? "";
  const providers = (await (await other.request.get("/api/providers")).json()) as { id: number; kind: string }[];
  const mock = providers.find((p) => p.kind === "mock")!;
  const agent = await (await other.request.post("/api/agents", {
    headers: { "X-CSRF-Token": csrf },
    data: { name: `Live ${Date.now() % 100000}`, role: "backend", execution_engine: "api", provider_id: mock.id, model: "mock-model" },
  })).json();
  expect(agent.id, JSON.stringify(agent)).toBeTruthy();
  const r = await other.request.post("/api/executions", { headers: { "X-CSRF-Token": csrf }, data: { agent_id: agent.id, input: { prompt: "live" } } });
  expect(r.ok(), await r.text()).toBeTruthy();
  const exId = (await r.json()).id as string;

  await expect(page.getByTestId("ex-list")).toContainText(exId.slice(0, 8), { timeout: 15_000 });
  expect(await page.getByTestId("ex-list").locator("li").count()).toBeGreaterThan(before === 100 ? 99 : before);
});
