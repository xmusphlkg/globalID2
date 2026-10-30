import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { canonicalRoutes } from "./routes";

test("canonical pages meet automated WCAG 2.2 A/AA checks", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "A single browser is sufficient for semantic checks");
  test.setTimeout(120_000);

  for (const route of canonicalRoutes) {
    const response = await page.goto(route, { waitUntil: "domcontentloaded" });
    expect(response?.ok(), `${route} must return a successful document`).toBeTruthy();
    await expect(page, `${route} must expose the Control Center document title`).toHaveTitle(/GIDS Control Center/);
    await page.waitForTimeout(route === "/data/knowledge" ? 1_800 : 400);
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(result.violations, `${route} must not have automated accessibility violations`).toEqual([]);
  }
});

test("navigation search is labelled and keyboard dismissible", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Dialog semantics do not vary by viewport");
  await page.goto("/models/queue");
  await page.getByRole("button", { name: /Jump to/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search workspaces and tools" })).toBeFocused();
  const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(result.violations).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
});

test("mobile shell exposes a compact title, country context, and accessible drawer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile shell check runs in the mobile project");
  await page.goto("/overview/events?country=JP", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("banner").locator("p").filter({ hasText: "Events & signals" })).toBeVisible();
  await expect(page.getByText("Global scope", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"])
    .analyze();
  expect(result.violations.filter(item => ["critical", "serious"].includes(item.impact ?? ""))).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
});
