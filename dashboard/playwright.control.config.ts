import { defineConfig, devices } from "@playwright/test";

// Isolated, fixture-only acceptance: never reuse a production dashboard or BFF.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "control-plane.spec.ts",
  outputDir: "/tmp/globalid-workflow-center/results",
  reporter: [["list"], ["json", { outputFile: "/tmp/globalid-workflow-center/playwright-results.json" }]],
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 2,
  use: {
    baseURL: "http://127.0.0.1:3107",
    trace: "retain-on-failure",
    actionTimeout: 10_000,
    serviceWorkers: "block",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "tablet", use: { ...devices["iPad Pro 11"], browserName: "chromium" } },
    { name: "mobile", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
  webServer: {
    command: "GLOBALID_NEXT_DIST_DIR=.next-control-e2e API_PROXY_TARGET=http://127.0.0.1:1 npm run dev -- --hostname 127.0.0.1 --port 3107",
    url: "http://127.0.0.1:3107",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
