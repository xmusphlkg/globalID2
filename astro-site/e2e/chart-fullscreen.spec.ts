import { expect, test, type Locator, type Page } from '@playwright/test';

const source = {
  disease_id: 'D021', name_en: 'Brazil', name_zh: '巴西', name_fr: 'Brésil',
  dates: ['2024-01-01', '2024-02-01', '2025-01-01', '2025-02-01'],
  cases: [10, 20, 100, 200], deaths: [0, 1, 2, 3], total_cases: 330,
  incidence_rates: [1, 2, 10, 20], period_granularity: 'monthly',
  metric_type: 'case_notifications', reporting_basis: 'notifications',
  time_basis: 'report_date', definition_version: 'same', comparability: 'direct',
};

test.beforeEach(async ({ page }) => {
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => route.fulfill({ json: {
    countries: [], disease_coverage: { D021: { country_count: 2 }, D037: { country_count: 2 } },
  } }));
  for (const id of ['d021', 'd037']) {
    await page.route(`**/site-data/diseases/${id}.json`, route => route.fulfill({ json: {
      country_series: { AU: source, BR: source },
    } }));
  }
});

async function fillsFullscreen(page: Page, panel: Locator, labels = ['Enter full-screen', 'Exit full-screen']) {
  const chart = panel.locator('.echarts-for-react').first();
  await expect(chart.locator('canvas').first()).toBeVisible();
  const normal = (await chart.boundingBox())!;
  await panel.getByRole('button', { name: labels[0], exact: true }).click();
  await expect.poll(() => panel.evaluate(element => document.fullscreenElement === element)).toBe(true);
  const assertFits = async () => {
    await expect.poll(async () => (await chart.boundingBox())!.height).toBeGreaterThan(normal.height + 100);
    await expect.poll(async () => {
      const plot = (await chart.boundingBox())!;
      const canvas = (await chart.locator('canvas').first().boundingBox())!;
      return Math.abs(plot.height - canvas.height) + Math.abs(plot.width - canvas.width);
    }).toBeLessThanOrEqual(2);
    expect(await panel.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  };
  await assertFits();
  const fullHeight = (await chart.boundingBox())!.height;
  const viewport = page.viewportSize()!;
  // Chromium disallows resizing a native fullscreen window. Change the
  // emulated display instead, preserving the real Fullscreen API state.
  const display = await page.context().newCDPSession(page);
  await display.send('Emulation.setDeviceMetricsOverride', {
    ...viewport, height: viewport.height + 240, deviceScaleFactor: 1, mobile: false,
  });
  await expect.poll(async () => (await chart.boundingBox())!.height).toBeGreaterThan(fullHeight + 200);
  await assertFits();
  await panel.getByRole('button', { name: labels[1], exact: true }).click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
  await display.send('Emulation.setDeviceMetricsOverride', {
    ...viewport, deviceScaleFactor: 1, mobile: false,
  });
  await display.detach();
  // Detaching CDP clears its emulation; force Playwright to restore the
  // configured viewport even though its cached dimensions have not changed.
  await page.setViewportSize({ ...viewport, width: viewport.width + 1 });
  await page.setViewportSize(viewport);
  await expect.poll(async () => Math.abs((await chart.boundingBox())!.height - normal.height)).toBeLessThanOrEqual(1);
  await expect.poll(async () => Math.abs((await chart.locator('canvas').first().boundingBox())!.height - normal.height)).toBeLessThanOrEqual(1);
}

for (const [lang, labels] of [
  ['en', ['Enter full-screen', 'Exit full-screen']],
  ['zh', ['进入全屏', '退出全屏']],
  ['fr', ['Plein écran', 'Quitter le plein écran']],
] as const) {
  test(`comparison fills the screen, resizes and restores each result in ${lang}`, async ({ page }) => {
    await page.goto(`${lang === 'en' ? '' : `/${lang}`}/compare/?d=D021,D037&c=AU,BR`, { waitUntil: 'domcontentloaded' });
    const panels = page.locator('.compare-chart');
    await expect(panels).toHaveCount(2);
    for (const panel of await panels.all()) await fillsFullscreen(page, panel, [...labels]);
  });
}

test('seasonality heatmap fills the screen and restores its normal height', async ({ page }) => {
  await page.goto('/diseases/dengue/', { waitUntil: 'domcontentloaded' });
  await page.locator('#seasonality').scrollIntoViewIfNeeded();
  await fillsFullscreen(page, page.locator('.disease-seasonality'));
});

test('surveillance plot and its canvas grow together in fullscreen', async ({ page }) => {
  await page.setViewportSize({ ...page.viewportSize()!, height: 1400 });
  await page.goto('/diseases/dengue/', { waitUntil: 'domcontentloaded' });
  await page.locator('#main-trend').scrollIntoViewIfNeeded();
  await fillsFullscreen(page, page.locator('.surveillance-explorer'));
});

test('report monthly figures fill the screen and restore their normal height', async ({ page }) => {
  await page.goto('/countries/jp/reports/68/influenza/', { waitUntil: 'domcontentloaded' });
  const figure = page.locator('figure.chart-fullscreen-fill').filter({ has: page.locator('.chart-result-actions') }).last();
  await figure.scrollIntoViewIfNeeded();
  await fillsFullscreen(page, figure);
});

for (const [path, anchor, selector] of [
  ['/countries/', '.coverage-map-frame', '.coverage-map-shell'],
  ['/diseases/dengue/', '#coverage', '.disease-coverage-map'],
]) {
  test(`coverage map keeps filling fullscreen on ${path}`, async ({ page }) => {
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    await page.locator(anchor).scrollIntoViewIfNeeded();
    await fillsFullscreen(page, page.locator(selector));
  });
}

test('country heatmap and comparison table use their fullscreen space', async ({ page }) => {
  await page.setViewportSize({ ...page.viewportSize()!, height: 1400 });
  await page.route('**/site-data/countries/cl.json', route => route.fulfill({ json: {
    country_code: 'CL', disease_series: { D021: { ...source, name_en: 'Dengue', category: 'Viral' } },
  } }));
  await page.goto('/countries/cl/', { waitUntil: 'domcontentloaded' });
  const heatmap = page.locator('section.figure-panel').filter({ has: page.getByRole('heading', { name: 'Seasonal patterns', exact: true }) });
  await heatmap.scrollIntoViewIfNeeded();
  await fillsFullscreen(page, heatmap.locator('.chart-shell'));
  await page.locator('#disease-comparison-table').scrollIntoViewIfNeeded();
  const table = page.locator('.comparison-shell');
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await table.getByRole('button', { name: 'Enter full-screen', exact: true }).click();
  await expect.poll(() => table.evaluate(element => document.fullscreenElement === element)).toBe(true);
  const rect = (await table.boundingBox())!;
  expect(rect.width).toBe(page.viewportSize()!.width);
  expect(rect.height).toBe(page.viewportSize()!.height);
  await expect(table.locator('tbody tr')).toHaveCount(1);
  await table.getByRole('button', { name: 'Exit full-screen', exact: true }).click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true);
});
