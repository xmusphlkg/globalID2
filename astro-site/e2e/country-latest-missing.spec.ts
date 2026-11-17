import { expect, test } from '@playwright/test';
import botulism from './fixtures/cl-botulism-source-only.json' with { type: 'json' };

test('country comparison preserves source-only unknown totals and reported zero', async ({ page }) => {
  await page.route('**/site-data/{bootstrap,current}.json', route => route.fulfill({ status: 404 }));
  const entry = (id: string, en: string, value: number | null) => ({
    id, en, zh: en, fr: en, slug: id.toLowerCase(), cat: 'Bacterial',
    x: [0], c: [value], w: [], d: [id === 'UNKNOWN' ? 11 : value === 0 ? 0 : null],
    tc: value ?? 0, td: id === 'UNKNOWN' ? 11 : 0,
    lc: value, ld: null, period_granularity: 'annual',
  });
  const series = [
      entry('UNKNOWN', 'Unknown count', null),
      entry('ZERO', 'Reported zero', 0),
      entry('KNOWN', 'Reported count', 7),
      botulism,
  ];
  let scope: 'mixed' | 'source-only' | 'zero' = 'mixed';
  const currentSeries = () => scope === 'mixed' ? series : scope === 'source-only' ? [botulism] : [series[1]];
  await page.route('**/site-data/countries/cl.json', route => route.fulfill({ json: {
    v: 1, dates: ['2024-01-01'], series: currentSeries(),
  } }));
  await page.route('**/site-data/countries/cl-summary.json', route => route.fulfill({ json: {
    country_code: 'CL', total_cases: scope === 'mixed' ? 7 : 0, total_deaths: scope === 'mixed' ? 11 : 0,
    diseases: currentSeries().map(row => ({ disease_id: row.id, latest_cases: row.lc,
      latest_deaths: scope === 'mixed' ? null : [...row.d].reverse().find(value => value != null) ?? null })),
  } }));
  await page.goto('/countries/cl/');
  const table = page.locator('#disease-comparison-table');
  await table.scrollIntoViewIfNeeded();
  const unknown = table.locator('tbody tr').filter({ hasText: 'Unknown count' });
  const zero = table.locator('tbody tr').filter({ hasText: 'Reported zero' });
  const sourceOnly = table.locator('tbody tr').filter({ hasText: 'Botulism' });
  // Historical positive deaths remain available even when latest deaths are unknown.
  await expect(page.locator('.site-stat-grid').first().locator('dd').nth(1)).toHaveText('11');
  await expect(sourceOnly.locator('td').nth(3)).toHaveText('—');
  await expect(sourceOnly.locator('td').nth(4)).toHaveText('—');
  await expect(unknown.locator('td').nth(3)).toHaveText('—');
  await expect(zero.locator('td').nth(3)).toHaveText('0');
  await expect(table.locator('tbody tr').filter({ hasText: 'Reported count' }).locator('td').nth(3)).toHaveText('7');
  await expect(unknown.locator('td').nth(4)).toHaveText('—');
  await expect(unknown.locator('td').nth(4).locator('[aria-label]')).toHaveCount(0);
  await expect(zero.locator('td').nth(4).locator('.comparison-empty')).toHaveText('0');
  await expect(zero.locator('td').nth(4).locator('[aria-label]')).toContainText('2024');
  const sortColumn = async (name: string) => {
    const header = table.getByRole('columnheader', { name });
    await header.evaluate(element => {
      const table = element.closest('table')!;
      table.scrollIntoView({ block: 'center' });
      const scroller = table.parentElement!;
      scroller.scrollLeft += element.getBoundingClientRect().right - scroller.getBoundingClientRect().right + 16;
    });
    await header.click();
  };
  await sortColumn('Latest Cases');
  await expect(table.locator('tbody tr').nth(2)).toContainText('Unknown count');
  await expect(table.locator('tbody tr').last()).toContainText('Botulism');
  await sortColumn('Latest Cases');
  await expect(table.locator('tbody tr').nth(2)).toContainText('Unknown count');
  await expect(table.locator('tbody tr').last()).toContainText('Botulism');
  await sortColumn('Total Cases');
  await expect(table.locator('tbody tr').last()).toContainText('Botulism');
  await sortColumn('Total Cases');
  await expect(table.locator('tbody tr').last()).toContainText('Botulism');
  await page.locator('#comparison-disease-search').fill('Unknown count');
  await expect(table.locator('tfoot tr td').nth(2)).toHaveText('—');
  await page.getByRole('button', { name: 'Deaths', exact: true }).click();
  await expect(unknown.locator('td').nth(5)).toHaveText('11');
  await expect(table.locator('tfoot tr td').nth(3)).toHaveText('11');
  await page.locator('#comparison-disease-search').fill('Botulism');
  await expect(table.locator('tfoot tr td').nth(1)).toHaveText('—');
  await expect(table.locator('tfoot tr td').nth(2)).toHaveText('—');
  await expect(table.locator('tfoot tr td').nth(3)).toHaveText('—');
  const retained = await page.evaluate(async () => {
    const data = await fetch('/site-data/countries/cl.json').then(response => response.json());
    return data.series.find((entry: { id: string }) => entry.id === 'D091');
  });
  expect(retained.source_series).toEqual(botulism.source_series);
  expect(retained.source_series).toHaveLength(4);
  expect(retained.source_series.flatMap((series: { values: number[] }) => series.values)).toHaveLength(14);
  expect(retained.c).toEqual([]);
  scope = 'source-only';
  await page.reload();
  const indicators = page.locator('.site-stat-grid').first().locator('dd');
  await expect(indicators.nth(0)).toHaveText('—');
  await expect(indicators.nth(1)).toHaveText('—');
  scope = 'zero';
  await page.reload();
  await expect(indicators.nth(0)).toHaveText('0');
  await expect(indicators.nth(1)).toHaveText('0');
});
