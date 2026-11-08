import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

// The production bundle is started separately. This suite must never start a
// development server or attach to the live control panel on port 3000.
const baseURL = process.env.REALTIME_E2E_BASE_URL || "http://127.0.0.1:3112";
const target = new URL(baseURL);
if (target.hostname !== "127.0.0.1" || target.port !== "3112") {
  throw new Error("Realtime acceptance requires the isolated loopback server on port 3112");
}
const artifacts = resolve(__dirname, "../data/cache/build-verification/20261107-realtime-control-panel/browser");

export default defineConfig({
  testDir: "./e2e",
  testMatch: "realtime-publish.spec.ts",
  outputDir: resolve(artifacts, "results"),
  reporter: [["list"], ["json", { outputFile: resolve(artifacts, "playwright-results.json") }]],
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 2,
  use: {
    baseURL, serviceWorkers: "block", trace: "retain-on-failure",
    actionTimeout: 10_000, contextOptions: { reducedMotion: "reduce" },
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["iPhone 15"], browserName: "chromium" } },
  ],
});
