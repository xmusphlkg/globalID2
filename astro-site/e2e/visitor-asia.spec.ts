import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

test.setTimeout(180_000);
test.beforeEach(async ({ page }) => {
  page.setDefaultTimeout(30_000);
  // Analytics are outside these visitor tasks and can delay document readiness.
  await page.route('https://stats.globalinfectiousdisease.com/**', route => route.abort());
});

test('editing a shared analysis survives a language switch and reload', async ({ page }) => {
  await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/countries/jp.json', route => route.fulfill({ json: {
    country_code: 'JP', disease_series: { D038: {
      disease_id: 'D038', name_en: 'Influenza', name_zh: '流行性感冒', slug: 'influenza',
      dates: ['2025-01-01', '2026-01-01', '2026-02-01'], cases: [10, 20, 30], deaths: [null, null, null],
      incidence_rates: [], total_cases: 60, period_granularity: 'monthly', comparability: 'direct',
    } },
  } }));
  await page.goto('/countries/jp/', { waitUntil: 'commit' });
  await page.locator('#trends').scrollIntoViewIfNeeded();
  const curve = page.getByTestId('surveillance-explorer');
  await curve.getByRole('button', { name: 'Share configuration', exact: true }).click();
  const shared = await curve.locator('.surveillance-share input').inputValue();
  await page.goto(shared, { waitUntil: 'commit' });
  await page.locator('#trends').scrollIntoViewIfNeeded();
  await curve.getByLabel('Start date', { exact: true }).fill('2026-01-01');
  await page.locator('.site-language-selector summary').click();
  await page.locator('.site-language-selector a[hreflang="zh-CN"]').click();
  await page.waitForURL('**/zh/countries/jp/**', { waitUntil: 'commit' });
  await page.locator('#trends').scrollIntoViewIfNeeded();
  await expect(curve.getByLabel('开始日期', { exact: true })).toHaveValue('2026-01-01');
  await page.reload({ waitUntil: 'commit' });
  await page.locator('#trends').scrollIntoViewIfNeeded();
  await expect(curve.getByLabel('开始日期', { exact: true })).toHaveValue('2026-01-01');
  await curve.getByRole('tab', { name: '数据', exact: true }).click();
  await expect(curve.getByTestId('surveillance-data').locator('tbody tr')).toHaveCount(2);
});

test.describe('Chinese public-health student on a phone', () => {
  test.use({ locale: 'zh-CN', timezoneId: 'Asia/Shanghai', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  test('finds dengue, compares Thailand and Japan, exports a chart and shares across languages', async ({ page }, testInfo) => {
    test.skip(process.env.GIDS_VISITOR_LIVE_DATA !== '1', 'Requires the read-only published-data visitor server.');
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/zh/', { waitUntil: 'commit' });
    await page.waitForLoadState('domcontentloaded');
    await page.locator('#site-search-open').click();
    await page.locator('#site-search-input').fill('登革热');
    await expect(page.locator('.site-search-result').first()).toHaveAttribute('href', '/zh/diseases/dengue/', { timeout: 30_000 });
    await page.locator('#site-search-input').press('Enter');
    await page.waitForURL('**/zh/search/?q=*', { waitUntil: 'commit' });
    await page.locator('.search-result-card[href="/zh/diseases/dengue/"]').click();
    await page.locator('#main-trend').scrollIntoViewIfNeeded();
    const curve = page.getByTestId('surveillance-explorer');
    await expect(curve.locator('.surveillance-selected')).toContainText('泰国');
    await curve.getByRole('button', { name: '12M', exact: true }).click();
    await curve.getByRole('tab', { name: '数据', exact: true }).click();
    await expect(curve.getByTestId('surveillance-data').locator('tbody tr').first()).toBeVisible();
    await page.getByRole('link', { name: '比较区域趋势', exact: true }).click();
    const countries = page.locator('.compare-filter-panel').nth(1);
    await expect(countries.locator('input[type="checkbox"]').first()).toBeAttached();
    while (await countries.locator('input:checked').count()) {
      await countries.locator('input:checked').first().uncheck();
    }
    for (const code of ['TH', 'JP']) {
      await countries.getByRole('searchbox').fill(code);
      await countries.getByRole('checkbox', { name: new RegExp(` ${code}$`) }).check();
    }
    await countries.getByRole('searchbox').fill('不存在的地区xyz');
    await expect(countries.locator('.compare-no-matches')).toHaveText('没有匹配项');
    await countries.getByRole('searchbox').fill('');
    await expect(countries.locator('input:checked')).toHaveCount(2);
    const chart = page.locator('.compare-chart').filter({ has: page.locator('canvas') });
    await expect(chart.locator('canvas')).toBeVisible();
    const downloadPromise = page.waitForEvent('download');
    await chart.getByRole('button', { name: '下载 PNG', exact: true }).click();
    const download = await downloadPromise;
    const downloadPath = testInfo.outputPath('dengue-thailand-japan.png');
    await download.saveAs(downloadPath);
    expect((await readFile(downloadPath)).subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a');
    await chart.getByRole('button', { name: '分享配置', exact: true }).click();
    const shared = new URL(await chart.locator('.chart-share-link input').inputValue());
    expect(shared.searchParams.get('c')).toBe('TH,JP');
    await page.locator('.site-language-selector summary').click();
    await page.locator('.site-language-selector a[hreflang="en"]').click();
    await page.waitForURL('**/compare/?*', { waitUntil: 'commit' });
    await expect(countries.locator('input:checked')).toHaveCount(2);
    expect(new URL(page.url()).searchParams.get('c')).toBe('TH,JP');
    await page.reload({ waitUntil: 'commit' });
    await expect(countries.locator('input:checked')).toHaveCount(2);
    await page.goBack({ waitUntil: 'commit' });
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
    await expect(countries.locator('input:checked')).toHaveCount(2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath('china-compare.png'), fullPage: true });
    expect(errors).toEqual([]);
  });
});

test.describe('Japanese researcher using English on desktop', () => {
  test.use({ locale: 'en-US', timezoneId: 'Asia/Tokyo', viewport: { width: 1440, height: 900 } });
  test('finds Japan, inspects influenza and reaches a report whose card and body agree', async ({ page }, testInfo) => {
    test.skip(process.env.GIDS_VISITOR_LIVE_DATA !== '1', 'Requires the read-only published-data visitor server.');
    await page.goto('/', { waitUntil: 'commit' });
    await page.waitForLoadState('domcontentloaded');
    await page.locator('#site-search-open').click();
    await page.locator('#site-search-input').fill('Japan');
    await page.locator('.site-search-result[href="/countries/jp/"]').click();
    await page.locator('#trends').scrollIntoViewIfNeeded();
    const curve = page.getByTestId('surveillance-explorer');
    await expect(curve.locator('.surveillance-selected')).toContainText('Influenza');
    await curve.getByRole('button', { name: '12M', exact: true }).click();
    await curve.getByRole('tab', { name: 'Data', exact: true }).click();
    await expect(curve.getByTestId('surveillance-data').locator('tbody tr').first()).toBeVisible();
    await curve.locator('.surveillance-picker-desktop').getByRole('searchbox').fill('zzzznotadisease');
    await expect(curve.locator('.surveillance-picker-desktop input[type="checkbox"]')).toHaveCount(0);
    await curve.locator('.surveillance-picker-desktop').getByRole('searchbox').fill('Influenza');
    await expect(curve.locator('.surveillance-picker-desktop input[type="checkbox"]').first()).toBeVisible();
    await page.locator('#reports').scrollIntoViewIfNeeded();
    const card = page.locator('#reports a[data-seo-event="report_open"]').first();
    const cardTitle = (await card.locator('h3').innerText()).trim();
    expect(cardTitle).toContain('Japan Infectious Disease Decision Brief');
    expect(await card.innerText()).toContain('The current source does not provide death-count fields');
    await card.click();
    await expect(page.locator('main h1')).toHaveText(cardTitle);
    await expect(page.locator('main')).toContainText('missing values should not be interpreted as absence of mortality burden');
    await page.screenshot({ path: testInfo.outputPath('japan-report.png'), fullPage: true });
    await page.goBack({ waitUntil: 'commit' });
    await page.locator('#trends').scrollIntoViewIfNeeded();
    await expect(curve.locator('.surveillance-selected')).toContainText('Influenza');
    await page.locator('#downloads').scrollIntoViewIfNeeded();
    const sourceLinks = page.locator('#downloads a[href^="https://"]');
    await expect(sourceLinks.first()).toBeVisible();
    expect(await sourceLinks.evaluateAll(links => links.some(link => /niid|jihs|mhlw/i.test((link as HTMLAnchorElement).href)))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
