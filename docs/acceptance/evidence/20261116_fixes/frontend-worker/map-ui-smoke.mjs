import { chromium, expect } from '/home/likangguo/globalID/globalID2/astro-site/node_modules/@playwright/test/index.mjs';
import { writeFile, readFile } from 'node:fs/promises';
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const width of [390, 1280]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
    await page.goto('http://127.0.0.1:4322/countries/us/?mapDisease=D094&mapFrom=2025-01-01&mapTo=2025-12-31&mapCompare=none#regional-distribution', { timeout: 90000 });
    const map = page.locator('[data-subdivision-country="US"]');
    await map.scrollIntoViewIfNeeded();
    await expect(map.getByRole('combobox', { name: 'Disease', exact: true })).toHaveValue('D094', { timeout: 30000 });
    await expect(map.getByLabel('Start date', { exact: true })).toHaveValue('2025-01-01', { timeout: 30000 });
    await map.getByRole('tab', { name: 'Data', exact: true }).click();
    await expect(map.locator('.subdivision-map-table tbody tr')).toHaveCount(56);
    const rates = await map.locator('.subdivision-map-table tbody').innerText();
    await map.getByRole('combobox', { name: 'Group trend by', exact: true }).selectOption('annual');
    await expect.poll(() => map.locator('.subdivision-map-table tbody').innerText()).toBe(rates);
    await map.getByRole('combobox', { name: 'Measure', exact: true }).selectOption('cases');
    await expect(map.locator('.subdivision-map-table thead')).toContainText('Reported cases');
    await expect.poll(() => map.locator('.subdivision-map-table tbody').innerText()).not.toBe(rates);
    const downloaded = page.waitForEvent('download');
    await map.getByRole('button', { name: 'Download data', exact: true }).click();
    const download = await downloaded;
    const csv = await readFile(await download.path(), 'utf8');
    expect(download.suggestedFilename()).toBe('US-D094-2025-01-01-2025-12-31.csv');
    expect(csv).toContain('US-CA');
    expect(csv).toContain('2025-01-01');
    await map.getByLabel('Start date', { exact: true }).fill('2026-01-01');
    await expect(map.getByRole('alert')).toBeVisible();
    await expect(map.getByRole('button', { name: 'Download data', exact: true })).toBeDisabled();
    await map.getByLabel('Start date', { exact: true }).fill('2025-01-01');
    await expect(map.getByRole('alert')).toHaveCount(0);
    await expect(map.getByRole('button', { name: 'Download data', exact: true })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    expect(errors).toEqual([]);
    await page.screenshot({ path: `/tmp/globalid-fixes-20261116/map-ui-${width}.png` });
    results.push({ width, country: 'US', disease: 'D094', regions: 56, view_preserves_totals: true, metric_changes_values: true, csv_download: true, invalid_dates_block_export: true, horizontal_overflow: false, page_errors: errors });
    await context.close();
  }
} finally {
  await browser.close();
  await writeFile('/tmp/globalid-fixes-20261116/map-ui-smoke.json', JSON.stringify(results, null, 2));
}
console.log(JSON.stringify(results));
