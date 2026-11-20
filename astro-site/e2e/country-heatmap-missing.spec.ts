import { expect, test } from '@playwright/test';

const months = ['2024-01', '2024-02', '2024-03', '2024-04'];
const series = [
  { id: 'D021', en: 'Dengue', zh: '登革热', x: [0, 1, 2], c: [0, null, 10], d: [], w: [], period_granularity: 'monthly' },
  { id: 'D038', en: 'Influenza', zh: '流行性感冒', x: [3], c: [2], d: [], w: [], period_granularity: 'monthly' },
  { id: 'D042', en: 'Annual source', zh: '年度来源', x: [4], c: [999], d: [], w: [], period_granularity: 'annual' },
];

for (const precomputed of [true, false]) {
  test(`country heatmap distinguishes missing data from observed zero (${precomputed ? 'published' : 'fallback'})`, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/countries/cl.json', route => route.fulfill({ json: {
      v: 1,
      dates: [...months.map(month => `${month}-01`), '2023-01-01'],
      series,
      ...(precomputed ? { heatmap: {
        months,
        disease_ids: ['D021', 'D038'],
        z: [[0, null, Math.log10(11), null], [null, null, null, Math.log10(3)]],
      } } : {}),
    } }));
    await page.goto('/countries/cl/', { waitUntil: 'domcontentloaded' });
    const panel = page.locator('section.figure-panel').filter({
      has: page.getByRole('heading', { name: 'Seasonal patterns', exact: true }),
    });
    await panel.scrollIntoViewIfNeeded();
    await expect(panel.locator('canvas').first()).toBeVisible();
    await panel.getByRole('tab', { name: 'Data', exact: true }).click();
    await expect(panel.locator('tbody tr')).toHaveCount(2);
    await expect(panel.locator('table')).not.toContainText('Annual source');
    const dengue = panel.locator('tbody tr').filter({ hasText: 'Dengue' });
    const influenza = panel.locator('tbody tr').filter({ hasText: 'Influenza' });
    await expect(dengue.locator('td')).toHaveText(['Dengue', '0', '—', '10', '—']);
    await expect(influenza.locator('td')).toHaveText(['Influenza', '—', '—', '—', '2']);
    await panel.getByRole('tab', { name: 'Chart', exact: true }).click();
    await expect(panel.locator('canvas').first()).toBeVisible();
    expect(pageErrors).toEqual([]);
  });
}
