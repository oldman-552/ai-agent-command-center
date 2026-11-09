import { defineConfig } from "@playwright/test";

// E2E against a running stack (BASE_URL, default http://localhost:3000).
// Uses an installed Chrome when CHROME_PATH is set, else Playwright's bundled Chromium.
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    launchOptions: process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {},
    screenshot: "only-on-failure",
  },
});
