import { expect, test } from "@playwright/test";

test("legacy task route permanently redirects into the operations workspace", async ({ page }) => {
  await page.goto("/sources/tasks");
  await expect(page).toHaveURL(/\/operations\/tasks(?:\?.*)?$/);
});

test("the five workspace navigation is available", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/overview(?:\?.*)?$/);
  const mobileMenu = page.getByRole("button", { name: "Open navigation" });
  if (await mobileMenu.isVisible()) await mobileMenu.click();
  for (const label of ["Overview", "Ingestion & Tasks", "Data Governance", "AI & Reports", "Settings"]) {
    await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  }
});
