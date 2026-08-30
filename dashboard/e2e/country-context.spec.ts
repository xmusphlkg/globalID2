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
  const country = page.getByLabel("Active country or region");
  for (const selection of [
    { label: "Austria", code: "AT" },
    { label: "Germany", code: "DE" },
    { label: "Japan", code: "JP" },
    { label: "New Zealand", code: "NZ" },
  ]) {
    await country.click();
    const search = page.getByLabel("Search countries, regions, or codes");
    await search.fill(selection.code);
    await page.getByTestId(`jurisdiction-option-${selection.code}`).click();
    await expect(page).toHaveURL(new RegExp(`[?&]country=${selection.code}(?:&|$)`));
    await expect(country).toContainText(selection.label);
    await page.waitForTimeout(500);
  }

  await country.click();
  await page.getByLabel("Search countries, regions, or codes").fill("CN-SH");
  await page.getByTestId("jurisdiction-option-CN-SH").click();
  await expect(page).toHaveURL(/[?&]country=CN-SH(?:&|$)/);
  await expect(country).toContainText("Shanghai");

  expect(errors).toEqual([]);
});
