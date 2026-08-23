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

test("AI creation dialogs are labelled and keyboard dismissible", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Dialog semantics do not vary by viewport");
  await page.goto("/production/ai?country=JP");

  for (const buttonName of ["New AI Task", "Update Disease Knowledge"]) {
    await page.getByRole("button", { name: buttonName }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(result.violations, `${buttonName} dialog must be accessible`).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
  }
});

test("mobile shell exposes a compact title, country context, and accessible drawer", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Mobile shell check runs in the mobile project");
  await page.goto("/overview/events?country=JP", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("banner").locator("p").filter({ hasText: "Events & Signals" })).toBeVisible();
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
