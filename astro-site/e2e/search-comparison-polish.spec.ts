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
  await expect(countriesPanel.getByRole('checkbox', { name: '奥地利 AT', exact: true })).toBeChecked();
  await diseasePanel.getByRole('searchbox').fill('流感');
  await expect(diseasePanel.getByRole('checkbox', { name: /^流行性感冒 \d/ })).toBeVisible();
  await diseasePanel.getByRole('checkbox', { name: /^流行性感冒 \d/ }).check();
  await diseasePanel.getByRole('searchbox').fill('疟疾');
  await diseasePanel.getByRole('checkbox', { name: /^疟疾 \d/ }).uncheck();
  await expect(page.getByRole('status').filter({ hasText: '已移除' })).toContainText('AT');
  await expect(countriesPanel.locator('input:checked')).toHaveCount(1);
  await expect(page).toHaveURL(/d=D038&c=AU&u=count/);
  await countriesPanel.getByRole('checkbox', { name: '日本 JP', exact: true }).check();
  const languageMenu = page.locator('.site-language-selector');
  await languageMenu.locator('summary').click();
  await languageMenu.locator('a[hreflang="en"]').click();
  await expect(page).toHaveURL(/\/compare\/\?d=D038&c=AU%2CJP&u=count/);
  await expect(countriesPanel.locator('input:checked')).toHaveCount(2);
});

const comparisonAreas = [
  { code: 'AU', name_en: 'Australia', name_zh: '澳大利亚', name_fr: 'Australie' },
  { code: 'CN', name_en: 'China', name_zh: '中国', name_fr: 'Chine' },
  { code: 'CN-BJ', name_en: 'Beijing', name_zh: '北京市', name_fr: 'Pékin', parent_code: null, source_info: { parent_country_code: 'CN' } },
  { code: 'CN-SH', name_en: 'Shanghai', name_zh: '上海市', name_fr: 'Shanghai', parent_code: 'CN' },
  { code: 'CN-GD', name_en: 'Guangdong', name_zh: '广东省', name_fr: 'Guangdong', parent_code: 'CN' },
  { code: 'US', name_en: 'United States', name_zh: '美国', name_fr: 'États-Unis' },
  { code: 'US-CA', name_en: 'California', name_zh: '加利福尼亚州', name_fr: 'Californie', source_info: { parent_country_code: 'US' } },
];
for (const language of ['en', 'zh', 'fr'] as const) {
  test(`comparison selects published lower-level areas without overflowing in ${language}`, async ({ page }) => {
    await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/meta.json', route => route.fulfill({ json: { countries: comparisonAreas, disease_coverage: { D037: { country_count: 1 } } } }));
    await page.route('**/site-data/diseases/d037.json', route => route.fulfill({ json: { country_series: {
      AU: source('Malaria'), 'CN-BJ': source('Malaria'), 'CN-SH': source('Malaria'), 'US-CA': source('Malaria'), 'CN-GD': source('Malaria', []),
    } } }));
    await page.goto(`${language === 'en' ? '' : `/${language}`}/compare/?d=D037&c=AU,CN-BJ&u=count`);
    const panel = page.locator('.compare-filter-panel').nth(1);
    const areaName = (code: string) => `${comparisonAreas.find(area => area.code === code)![`name_${language}`]} ${code}`;
    const beijing = panel.getByRole('checkbox', { name: areaName('CN-BJ'), exact: true });
    await expect(beijing).toBeChecked();
    await expect(panel.getByRole('checkbox', { name: areaName('CN'), exact: true })).toHaveCount(0);
    await expect(panel.getByRole('checkbox', { name: areaName('CN-GD'), exact: true })).toHaveCount(0);
    const expand = panel.getByRole('button').filter({ hasText: '(2)' });
    await expand.click();
    await expect(beijing).toBeHidden();
    await expand.click();
    await panel.getByRole('checkbox', { name: areaName('CN-SH'), exact: true }).check();
    await panel.getByRole('searchbox').fill('US-CA');
    await panel.getByRole('checkbox', { name: areaName('US-CA'), exact: true }).check();
    await panel.getByRole('searchbox').fill('');
    await expect(panel.locator('legend')).toContainText('4/4');
    expect(new URL(page.url()).searchParams.get('c')).toBe('AU,CN-BJ,CN-SH,US-CA');
    await expect(page.locator('.compare-chart-plot canvas')).toBeVisible();
    const widths = await page.locator('.compare-filter-panel, .compare-choice-list, .compare-choice-list label, .compare-area-heading').evaluateAll(elements => elements.filter(element => {
      const box = element.getBoundingClientRect();
      return box.width > 0 && element.scrollWidth > element.clientWidth + 1;
    }).map(element => ({ className: element.className, width: element.clientWidth, scrollWidth: element.scrollWidth })));
    expect(widths).toEqual([]);
    const headings = await page.locator('.compare-filter-panel').evaluateAll(panels => panels.map(panel => {
      const frame = panel.getBoundingClientRect();
      const title = panel.querySelector('legend')!.getBoundingClientRect();
      return { top: title.top - frame.top, left: title.left - frame.left, right: frame.right - title.right };
    }));
    for (const heading of headings) {
      expect(heading.top).toBeGreaterThanOrEqual(16);
      expect(heading.left).toBeGreaterThanOrEqual(16);
      expect(heading.right).toBeGreaterThanOrEqual(16);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.reload();
    await expect(panel.locator('input:checked')).toHaveCount(4);
    await expect(beijing).toBeChecked();
  });
}

test('theme preference follows the reader across all three public hosts', async ({ context, page, request, baseURL }) => {
  // Serve the local build under the real hostnames inside this test browser;
  // no request or preference is sent to the production sites.
  await context.route(/^https:\/\/(?:papers\.|reports\.)?globalinfectiousdisease\.com\//, async route => {
    const target = new URL(route.request().url());
    const response = await request.get(`${baseURL}${target.pathname}${target.search}`);
    await route.fulfill({ response });
  });
  // These fixture pages hydrate after `load`. Drain their intercepted asset
  // requests before a cross-host navigation cancels them underneath the local
  // proxy's asynchronous fetch/fulfill pair. No route errors are suppressed.
  const visit = async (url: string) => {
    await page.waitForLoadState('networkidle');
    await page.goto(url, { waitUntil: 'networkidle' });
  };
  try {
    await visit('https://globalinfectiousdisease.com/zh/');
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    expect((await context.cookies()).find(cookie => cookie.name === 'gids_theme')?.domain).toBe('.globalinfectiousdisease.com');
    await visit('https://papers.globalinfectiousdisease.com/zh/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await visit('https://reports.globalinfectiousdisease.com/zh/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.locator('#theme-toggle').click();
    await visit('https://globalinfectiousdisease.com/zh/');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  } finally {
    await context.unrouteAll({ behavior: 'wait' });
  }
});
