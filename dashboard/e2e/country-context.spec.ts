import { expect, test } from "@playwright/test";

test("country context updates the URL and source data without client errors", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Country behaviour is viewport independent");
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });

  await page.goto("/operations/sources?country=US");
  const country = page.getByLabel("Active country");
  for (const selection of [
    { label: "Austria", code: "AT" },
    { label: "Germany", code: "DE" },
    { label: "Japan", code: "JP" },
    { label: "New Zealand", code: "NZ" },
  ]) {
    await country.selectOption({ label: selection.label });
    await expect(page).toHaveURL(new RegExp(`[?&]country=${selection.code}(?:&|$)`));
    await expect(country.locator("option:checked")).toHaveText(selection.label);
    await page.waitForTimeout(500);
  }

  expect(errors).toEqual([]);
});
