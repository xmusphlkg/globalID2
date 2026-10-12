import { expect, test, type Page, type Locator } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const base = {
  disease_id: "D021",
  dates: ["2024-01-01", "2024-02-01", "2025-01-01", "2025-02-01"],
  cases: [10, 20, 100, 200],
  deaths: [0, 1, 2, 3],
  weekly_equiv_cases: [],
  total_cases: 330,
  incidence_rates: [1, 2, 10, 20],
  period_granularity: "monthly",
  metric_type: "case_notifications",
  reporting_basis: "notifications",
  time_basis: "report_date",
  definition_version: "same",
  comparability: "direct",
};
async function fixture(page: Page) {
  await page.route("**/site-data/diseases/d021.json", (route) =>
    route.fulfill({
      json: {
        country_series: {
          BR: {
            ...base,
            name_en: "Brazil",
            name_zh: "巴西",
            name_fr: "Brésil",
          },
          "CN-ZJ": {
            ...base,
            name_en: "Zhejiang",
            name_zh: "浙江省",
            name_fr: "Zhejiang",
            cases: [20, 40, 200, 400],
            incidence_rates: [2, 4, 20, 40],
            source_series: [
              {
                series_code: "ZJ_SOURCE",
                selection_key: "ZJ_SOURCE::sex:female::9b46df10f340",
                source_label: "Source A",
                dimension_key: "sex:female",
                dimensions: { sex: "female" },
                temporal_granularity: "monthly",
                metric_type: "case_notifications",
                reporting_basis: "notifications",
                time_basis: "report_date",
                definition_version: "same",
                comparability: "direct",
              },
            ],
          },
          "CN-GD": {
            ...base,
            name_en: "Guangdong",
            name_zh: "广东省",
            name_fr: "Guangdong",
            incidence_rates: [null, null, null, null],
          },
          US: {
            ...base,
            name_en: "United States",
            name_zh: "美国",
            name_fr: "États-Unis",
            comparability: "not_comparable",
          },
        },
      },
    }),
  );
  await page.goto("/diseases/dengue/", { waitUntil: "domcontentloaded" });
  await page.locator("#trends").scrollIntoViewIfNeeded();
  const curve = page.getByTestId("surveillance-explorer");
  await expect(curve).toBeVisible();
  return curve;
}
async function picker(curve: Locator) {
  const mobileButton = curve.getByRole("button", {
    name: "Choose series",
    exact: true,
  });
  if (await mobileButton.isVisible()) {
    await mobileButton.click();
    return curve.locator("dialog");
  }
  return curve.locator(".surveillance-picker-desktop");
}
async function closePicker(page: Page, curve: Locator) {
  if (await curve.locator("dialog").isVisible())
    await page.keyboard.press("Escape");
}

test("selection can be cleared and the table stays empty", async ({ page }) => {
  const curve = await fixture(page);
  await curve.getByRole("button", { name: "Clear selection", exact: true }).click();
  await expect(curve.getByText("Choose a series to view data.", { exact: true })).toBeVisible();
  await curve.getByRole("tab", { name: "Data", exact: true }).click();
  await expect(curve.getByTestId("surveillance-data").locator("tbody tr")).toHaveCount(0);
  await expect(curve.getByRole("button", { name: /Download/ })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("adding a second series from a trend opens a comparison", async ({ page }) => {
  const curve = await fixture(page);
  const choices = await picker(curve);
  await choices.locator('input[value="BR::published"]').check();
  await closePicker(page, curve);
  await expect(curve.locator(".surveillance-selected > button")).toHaveCount(3);
  await expect(curve.locator(".surveillance-selected")).toContainText("Brazil");
  await expect(curve.locator(".surveillance-selected")).toContainText("Zhejiang");
});

test("many comparable series are paged as separate panels", async ({ page }) => {
  const codes = ["AU", "BR", "CN", "JP", "NO", "US"];
  await page.route("**/site-data/diseases/d021.json", (route) =>
    route.fulfill({
      json: {
        country_series: Object.fromEntries(codes.map((code) => [code, {
          ...base,
          name_en: code,
          name_zh: code,
        }])),
      },
    }),
  );
  await page.goto("/diseases/dengue/", { waitUntil: "domcontentloaded" });
  await page.locator("#trends").scrollIntoViewIfNeeded();
  const curve = page.getByTestId("surveillance-explorer");
  await expect(curve).toBeVisible();
  const choices = await picker(curve);
  await choices.getByRole("button", { name: /Select filtered defaults/ }).click();
  await closePicker(page, curve);
  const pager = curve.locator(".surveillance-pager");
  await expect(pager).toContainText("1/2");
  await expect(curve.locator(".surveillance-legend-item")).toHaveCount(4);
  await pager.getByRole("button", { name: "Next" }).click();
  await expect(pager).toContainText("2/2");
  await expect(curve.locator(".surveillance-legend-item")).toHaveCount(2);
});


test("adding an incompatible source updates the query and explains the limitation", async ({ page }) => {
  const curve = await fixture(page);
  const choices = await picker(curve);
  await choices.locator('input[value="US::published"]').check();
  await closePicker(page, curve);
  await expect(curve.locator(".surveillance-selected")).toContainText("United States");
  await expect(curve.getByTestId("surveillance-plot")).toBeVisible();
  await expect(curve.locator(".surveillance-status")).toBeVisible();
});

test("surveillance applies the time window in data and restores a shared query", async ({
  page,
}) => {
  const curve = await fixture(page);
  await curve.getByLabel("Start date", { exact: true }).fill("2025-01-01");
  await curve.getByRole("tab", { name: "Data", exact: true }).click();
  await expect(
    curve.getByTestId("surveillance-data").locator("tbody tr"),
  ).toHaveCount(2);
  await expect(curve.getByTestId("surveillance-data")).not.toContainText(
    "2024-",
  );
  await expect(curve.getByTestId("surveillance-data")).not.toContainText("2024-01-01");
  await expect(page.locator("#downloads")).toBeVisible();
  await curve
    .getByRole("button", { name: "Share configuration", exact: true })
    .click();
  const link = await curve.locator(".surveillance-share input").inputValue();
  await page.goto(link, { waitUntil: "domcontentloaded" });
  await page.locator("#trends").scrollIntoViewIfNeeded();
  await expect(
    page
      .getByTestId("surveillance-explorer")
      .getByLabel("Start date", { exact: true }),
  ).toHaveValue("2025-01-01");
});

test("surveillance preserves valid local rates when an added location lacks its denominator", async ({
  page,
}) => {
  const curve = await fixture(page);
  await expect(curve.locator('select[aria-label="Measure"] option[value="source_value"]')).toHaveAttribute("disabled", "");
  await expect(curve.locator('select[aria-label="Measure"] option[value="incidence_rates"]')).not.toHaveAttribute("disabled");
  await curve
    .getByLabel("Measure", { exact: true })
    .selectOption("incidence_rates");
  await curve.getByRole("tab", { name: "Data", exact: true }).click();
  await expect(
    curve.getByTestId("surveillance-data").locator("tbody tr"),
  ).toHaveCount(4);
  const choices = await picker(curve);
  await choices.locator('input[value="CN-GD::published"]').check();
  await closePicker(page, curve);
  await expect(curve.locator(".surveillance-selected")).toContainText("Guangdong");
  await expect(
    curve.getByTestId("surveillance-data").locator("tbody tr"),
  ).toHaveCount(4);
});

test("surveillance loads a specific source on demand and keeps published data separately selectable", async ({
  page,
}) => {
  let loads = 0;
  await page.route(
    "**/site-data/countries/cn-zj-source-series.json",
    (route) => {
      loads++;
      return route.fulfill({
        json: {
          v: 1,
          series: {
            D021: [
              {
                series_code: "ZJ_SOURCE",
                source_label: "Source A",
                temporal_granularity: "monthly",
                metric_type: "case_notifications",
                reporting_basis: "notifications",
                time_basis: "report_date",
                definition_version: "same",
                comparability: "direct",
                dates: base.dates,
                values: [2, 4, 20, 40],
              },
              {
                series_code: "ZJ_SOURCE",
                source_label: "Source A",
                dimension_key: "all",
                dimensions: {},
                temporal_granularity: "monthly",
                metric_type: "case_notifications",
                reporting_basis: "notifications",
                time_basis: "report_date",
                definition_version: "same",
                comparability: "direct",
                dates: base.dates,
                values: [5, 10, 50, 100],
              },
            ],
          },
        },
      });
    },
  );
  const curve = await fixture(page);
  expect(loads).toBe(0);
  const choices = await picker(curve);
  await choices
    .getByLabel("Search names or codes", { exact: true })
    .fill("Zhejiang");
  await choices.locator("details > summary").click();
  await expect.poll(() => loads).toBe(1);
  await choices.locator('input[value="CN-ZJ::ZJ_SOURCE"]').check();
  await closePicker(page, curve);
  await expect(curve.locator(".surveillance-selected")).toContainText(
    "Source A",
  );
  await expect(curve.locator(".surveillance-selected")).toContainText(
    "Zhejiang",
  );
  const dimensionChoices = await picker(curve);
  await dimensionChoices
    .getByLabel("Search names or codes", { exact: true })
    .fill("Zhejiang");
  const sourceOptions = dimensionChoices
    .locator(".surveillance-entity")
    .filter({ hasText: "Zhejiang" })
    .locator("details");
  if (!(await sourceOptions.evaluate((element: HTMLDetailsElement) => element.open)))
    await sourceOptions.locator("summary").click();
  const female = dimensionChoices
    .locator("label.surveillance-choice")
    .filter({ hasText: "sex: female" });
  await expect(female).toBeVisible();
  await female.locator("input").check();
  await closePicker(page, curve);
  await expect(curve.locator(".surveillance-selected")).toContainText(
    "sex: female",
  );
});

test("source options do not claim zero sources before loading", async ({ page }) => {
  await page.route("**/site-data/countries/br-source-series.json", (route) =>
    route.fulfill({ json: { v: 1, series: { D021: [] } } }),
  );
  const curve = await fixture(page);
  const choices = await picker(curve);
  await choices.getByLabel("Search names or codes", { exact: true }).fill("Brazil");
  const sourceDetails = choices.locator(".surveillance-entity details");
  await expect(sourceDetails.locator("summary")).toHaveAttribute("aria-label", "Source options");
  await sourceDetails.locator("summary").click();
  await expect(sourceDetails.locator("summary")).toHaveAttribute("aria-label", "Source options (0)");
  await expect(sourceDetails).toContainText("No additional registered sources");
});

test("surveillance empty date windows have an explicit recovery action", async ({
  page,
}) => {
  const curve = await fixture(page);
  await curve.getByLabel("Start date", { exact: true }).fill("2000-01-01");
  await curve.getByLabel("End date", { exact: true }).fill("2001-01-01");
  await expect(
    curve.getByRole("button", {
      name: "Show available time range",
      exact: true,
    }),
  ).toBeVisible();
  await curve.getByRole("tab", { name: "Data", exact: true }).click();
  await expect(
    curve.getByTestId("surveillance-data").locator("tbody tr"),
  ).toHaveCount(0);
  await curve.getByRole("button", { name: "Quality & coverage" }).click();
  await expect(curve.locator(".surveillance-quality-details")).toHaveAttribute("open", "");
  await curve
    .getByRole("button", { name: "Show available time range", exact: true })
    .click();
  await expect(
    curve.getByTestId("surveillance-data").locator("tbody tr"),
  ).toHaveCount(4);
});

test("surveillance controls and tables are accessible", async ({ page }) => {
  const curve = await fixture(page);
  const chartTab = curve.getByRole("tab", { name: "Chart", exact: true });
  await chartTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(
    curve.getByRole("tab", { name: "Data", exact: true }),
  ).toBeFocused();
  await curve.getByRole("tab", { name: "Data", exact: true }).click();
  const results = await new AxeBuilder({ page })
    .include(".surveillance-explorer")
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(
    results.violations.filter((item) =>
      ["critical", "serious"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
});

test("surveillance fullscreen control is visible and returns to the page", async ({ page }) => {
  const curve = await fixture(page);
  const enter = curve.getByRole("button", { name: "Enter full-screen", exact: true });
  await expect(enter).toBeVisible();
  await enter.click();
  await expect(curve).toHaveClass(/is-fullscreen/);
  await expect(curve.getByRole("button", { name: "Exit full-screen", exact: true })).toBeVisible();
  await curve.getByRole("button", { name: "Exit full-screen", exact: true }).click();
  await expect(curve).not.toHaveClass(/is-fullscreen/);
});

test("published country and disease datasets produce a usable default result", async ({
  page,
}) => {
  for (const [path, defaultSelection] of [
    ["/countries/au/", "COVID-19"],
    ["/diseases/dengue/", "Brazil"],
  ]) {
    await page.goto(path, { waitUntil: "domcontentloaded" });
    await page.locator("#trends").scrollIntoViewIfNeeded();
    const curve = page.getByTestId("surveillance-explorer");
    await expect(curve).toBeVisible({ timeout: 15_000 });
    await expect(curve.locator(".surveillance-selected")).toContainText(defaultSelection);
    await expect(curve.locator(".surveillance-selected > button")).toHaveCount(2);
    await curve.getByRole("tab", { name: "Data", exact: true }).click();
    await expect
      .poll(() =>
        curve.getByTestId("surveillance-data").locator("tbody tr").count(),
      )
      .toBeGreaterThan(0);
  }
});

test("research question opens a matching surveillance table and disease data link", async ({ page }) => {
  await page.goto("/research/ask/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: "Dengue in Brazil" }).click();
  await expect(page.locator("#answer-surveillance-card")).toBeVisible({ timeout: 30_000 });
  const curve = page.getByTestId("surveillance-explorer");
  await expect(curve).toBeVisible();
  await expect(curve).toHaveAttribute("data-context", /:BR$/);
  await curve.getByRole("tab", { name: "Data", exact: true }).click();
  const rows = await curve.getByTestId("surveillance-data").locator("tbody tr").count();
  expect(rows).toBeGreaterThan(0);
  await expect(curve.getByTestId("surveillance-data")).toContainText("Brazil");
  await expect(page.locator("#answer-surveillance-card a[href^='/diseases/']")).toBeVisible();
});

test("report curves disclose that their period uses currently published data", async ({
  page,
}) => {
  await page.goto("/fr/countries/jp/reports/50/covid-19/", {
    waitUntil: "domcontentloaded",
  });
  await page
    .getByRole("heading", { name: "Figures", exact: true })
    .scrollIntoViewIfNeeded();
  const curve = page.getByTestId("surveillance-explorer");
  await expect(curve).toBeVisible();
  await expect(curve).toContainText("pas un instantané historique");
  await expect(
    curve.getByRole("tab", { name: "Données", exact: true }),
  ).toBeVisible();
});
