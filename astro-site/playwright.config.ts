import { defineConfig } from '@playwright/test';

const widths = [360, 390, 768, 1280, 1440];

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  fullyParallel: true,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:4322',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: widths.map(width => ({
    name: `chromium-${width}`,
    use: { browserName: 'chromium', viewport: { width, height: width <= 390 ? 844 : 900 } },
  })),
  webServer: {
    // Keep the supervised test server in the foreground even when Playwright
    // runs under an agent-detected environment that normally backgrounds Astro.
    command: 'ASTRO_DEV_BACKGROUND=0 npm run dev -- --host 127.0.0.1 --port 4322',
    url: 'http://127.0.0.1:4322',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
