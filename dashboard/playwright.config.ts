import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";
const serverPort = new URL(baseURL).port || "3000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "tablet", use: { ...devices["iPad Pro 11"], browserName: "chromium" } },
    { name: "mobile", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
  webServer: {
    command: `npm run dev -- --hostname 127.0.0.1 --port ${serverPort}`,
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
