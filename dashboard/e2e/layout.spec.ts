import { expect, test } from "@playwright/test";
import { canonicalRoutes } from "./routes";

test("canonical pages render without client, API, or horizontal overflow errors", async ({ page }) => {
  test.setTimeout(120_000);
  const clientErrors: string[] = [];
  const apiErrors: string[] = [];
  let currentRoute = "not-started";

  page.on("pageerror", (error) => clientErrors.push(`${currentRoute}: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") {
      const location = message.location().url;
      clientErrors.push(`${currentRoute}: ${message.text()}${location ? ` (${location})` : ""}`);
    }
  });
  page.on("response", (response) => {
    if (response.status() >= 400) {
      const error = `${currentRoute}: ${response.status()} ${response.url()}`;
      if (response.url().includes("/api/")) apiErrors.push(error);
      else clientErrors.push(error);
    }
  });

  for (const route of canonicalRoutes) {
    currentRoute = route;
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(route === "/data/knowledge" ? 1_800 : 500);
    const dimensions = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    expect(dimensions.document, `${route} must fit the viewport`).toBeLessThanOrEqual(dimensions.viewport + 2);
  }

  expect(clientErrors).toEqual([]);
  expect(apiErrors).toEqual([]);
});
