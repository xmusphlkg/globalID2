import { expect, test } from '@playwright/test';

const searchEntries = [
  { id: 'disease:D100', kind: 'disease', title: { en: 'Haemophilus influenzae', zh: '流感嗜血杆菌' }, href: { en: '/diseases/haemophilus-influenzae/', zh: '/zh/diseases/haemophilus-influenzae/' }, aliases: ['Haemophilus influenzae'] },
  { id: 'disease:D038', kind: 'disease', title: { en: 'Influenza', zh: '流行性感冒' }, href: { en: '/diseases/influenza/', zh: '/zh/diseases/influenza/' }, aliases: ['流感', 'flu', 'grippe'] },
  { id: 'country:jp', kind: 'country', title: { en: 'Japan', zh: '日本' }, href: { en: '/countries/jp/', zh: '/zh/countries/jp/' }, aliases: ['Japan', '日本'] },
];

test('search distinguishes a failed index, retries, and ranks the ordinary disease first', async ({ page }) => {
  let available = false;
  await page.route('**/site-data/search-index.json', route => available ? route.fulfill({ json: { entries: searchEntries } }) : route.fulfill({ status: 503 }));
  await page.goto('/zh/');
  await page.locator('#site-search-open').click();
  await page.locator('#site-search-input').fill('流感');
  await expect(page.locator('#site-search-feedback')).toContainText('暂时无法加载搜索');
  await expect(page.locator('.site-search-result')).toHaveCount(0);
  available = true;
  await page.locator('#site-search-feedback').getByRole('button', { name: '重试' }).click();
  await expect(page.locator('.site-search-result strong').first()).toHaveText('流行性感冒');
  await expect(page.locator('.site-search-result-kind').first()).toHaveText('疾病');
  await page.locator('#site-search-input').fill('日本流感');
  await expect(page.locator('.site-search-result').first()).toHaveAttribute('href', /\/zh\/research\/ask\/\?q=/);
  await page.keyboard.press('Escape');
  await expect(page.locator('#site-search-open')).toBeFocused();
});

test('full search preserves its focused query and does not call a failed index an empty result', async ({ page }) => {
  let available = false;
  await page.route('**/site-data/search-index.json', route => available ? route.fulfill({ json: { entries: searchEntries } }) : route.fulfill({ status: 503 }));
  await page.goto('/zh/search/?q=日本流感');
  await expect(page.locator('#search-page-status')).toContainText('暂时无法加载搜索');
  available = true;
  await page.locator('#search-page-status').getByRole('button', { name: '重试' }).click();
  await expect(page.locator('.search-result-card').first()).toHaveAttribute('href', /\/zh\/research\/ask\/\?q=/);
});

const source = (name: string, dates: string[] = ['2026-10-04', '2026-10-11']) => ({ name_en: name, name_zh: name, dates, cases: [1, 2], incidence_rates: [], period_granularity: 'weekly' });
test('comparison validates country choices after changing disease and preserves URL on language switch', async ({ page }) => {
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => route.fulfill({ json: { disease_coverage: { D037: { country_count: 3 }, D038: { country_count: 2 } } } }));
  await page.route('**/site-data/diseases/d037.json', route => route.fulfill({ json: { country_series: { AU: source('Australia'), AT: source('Austria'), JP: source('Japan') } } }));
  await page.route('**/site-data/diseases/d038.json', route => route.fulfill({ json: { country_series: { AU: source('Australia'), JP: source('Japan') } } }));
  await page.goto('/zh/compare/?d=D037&c=AU,AT&u=count');
  const diseasePanel = page.locator('.compare-filter-panel').first();
  const countriesPanel = page.locator('.compare-filter-panel').nth(1);
  await expect(countriesPanel.getByRole('checkbox', { name: 'Austria AT', exact: true })).toBeChecked();
  await diseasePanel.getByRole('searchbox').fill('流感');
  await expect(diseasePanel.getByRole('checkbox', { name: /^流行性感冒 \d/ })).toBeVisible();
  await diseasePanel.getByRole('checkbox', { name: /^流行性感冒 \d/ }).check();
  await diseasePanel.getByRole('searchbox').fill('疟疾');
  await diseasePanel.getByRole('checkbox', { name: /^疟疾 \d/ }).uncheck();
  await expect(page.getByRole('status').filter({ hasText: '已移除' })).toContainText('AT');
  await expect(countriesPanel.locator('input:checked')).toHaveCount(1);
  await expect(page).toHaveURL(/d=D038&c=AU&u=count/);
  await countriesPanel.getByRole('checkbox', { name: 'Japan JP', exact: true }).check();
  const languageMenu = page.locator('.site-language-selector');
  await languageMenu.locator('summary').click();
  await languageMenu.locator('a[hreflang="en"]').click();
  await expect(page).toHaveURL(/\/compare\/\?d=D038&c=AU%2CJP&u=count/);
  await expect(countriesPanel.locator('input:checked')).toHaveCount(2);
});

test('theme preference follows the reader across all three public hosts', async ({ context, page, request, baseURL }) => {
  // Serve the local build under the real hostnames inside this test browser;
  // no request or preference is sent to the production sites.
  await context.route(/^https:\/\/(?:papers\.|reports\.)?globalinfectiousdisease\.com\//, async route => {
    const target = new URL(route.request().url());
    const response = await request.get(`${baseURL}${target.pathname}${target.search}`);
    await route.fulfill({ response });
  });
  await page.goto('https://globalinfectiousdisease.com/zh/');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  expect((await context.cookies()).find(cookie => cookie.name === 'gids_theme')?.domain).toBe('.globalinfectiousdisease.com');
  await page.goto('https://papers.globalinfectiousdisease.com/zh/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.goto('https://reports.globalinfectiousdisease.com/zh/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.locator('#theme-toggle').click();
  await page.goto('https://globalinfectiousdisease.com/zh/');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  // Theme-specific logos can still be loading after the final assertion. Drain
  // their local proxy callbacks before Playwright disposes the request context.
  await context.unrouteAll({ behavior: 'wait' });
});
