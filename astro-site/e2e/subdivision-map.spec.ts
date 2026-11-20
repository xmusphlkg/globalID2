import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Fixed source periods exercise zero, positive values, suppression and comparison.
const regions = [
  { code: 'BR-AC', name_en: 'Acre', name_zh: '阿克里州' },
  { code: 'BR-AL', name_en: 'Alagoas', name_zh: '阿拉戈斯州' },
  { code: 'BR-AM', name_en: 'Amazonas', name_zh: '亚马孙州' },
  { code: 'BR-AP', name_en: 'Amapa', name_zh: '阿马帕州' },
];
const boundary = { path: '/site-data/subdivision-maps/br/boundary.json', version: 'fixture', source: 'IBGE', source_url: 'https://ibge.gov.br/', license: 'CC0', license_url: 'https://creativecommons.org/publicdomain/zero/1.0/', historical_boundary_policy: 'current' };
test.beforeEach(async ({ page }) => {
  await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  const entries: Record<string, unknown> = {
    'index.json': { countries: [{ code: 'BR', path: '/site-data/subdivision-maps/br/index.json' }] },
    'br/index.json': { schema_version: 1, country_code: 'BR', expected_regions: 4, regions, boundary, population_path: '/site-data/subdivision-maps/br/population.json', diseases: [{ id: 'D028', labels: { en: 'Pertussis', zh: '百日咳', fr: 'Coqueluche' }, years: [2024, 2025], cadence: 'monthly', path: '/site-data/subdivision-maps/br/d028.json' }] },
    'br/boundary.json': { type: 'FeatureCollection', features: regions.map((region, index) => ({ type: 'Feature', properties: { name: region.code }, geometry: { type: 'Polygon', coordinates: [[[-65 + index * 4, -10], [-62 + index * 4, -10], [-62 + index * 4, -6], [-65 + index * 4, -6], [-65 + index * 4, -10]]] } })) },
    'br/population.json': { schema_version: 1, country_code: 'BR', regions: Object.fromEntries(regions.map(region => [region.code, Object.fromEntries([2024, 2025].map(year => [year, { year, value: 100000, source: 'IBGE', reference_date: `${year}-07-01`, basis: 'july_estimated_resident_population', source_url: 'https://ibge.gov.br/', version: 'fixture', method: 'same_calendar_year_no_interpolation' }]))])) },
    'br/d028.json': { schema_version: 1, country_code: 'BR', disease_id: 'D028', labels: { en: 'Pertussis', zh: '百日咳', fr: 'Coqueluche' }, years: [2024, 2025], cadence: 'monthly', comparison_key: 'same', case_basis: { en: 'Notifications', zh: '报告病例' }, regions: Object.fromEntries(regions.map((region, index) => [region.code, { comparison_key: 'same', data_layer: 'series_registry', semantics: { metric: 'case_notifications', definition: 'v1', reporting_basis: 'notification', time_basis: 'notification_month', source_series: ['TEST'] }, points: [['2024-01-01', [0, 1, 10, 1e7][index], 'observed'], ['2025-01-01', [0, 2, 20, null][index], index === 3 ? 'suppressed' : 'observed']] }])) },
  };
  for (const [key, value] of Object.entries(entries)) await page.route(`**/site-data/subdivision-maps/${key}`, route => route.fulfill({ json: value }));
});

test('regional navigation, compact filters, sortable selection, PNG and central data downloads', async ({ page }, testInfo) => {
  await page.goto('/zh/countries/br/?mapDisease=D028&mapFrom=2025-01-01&mapTo=2025-01-31&mapMetric=cases#regional-distribution', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('navigation', { name: '本页导航' }).getByRole('link', { name: '地区分布' })).toHaveAttribute('href', '#regional-distribution');
  const map = page.locator('#regional-distribution');
  await map.scrollIntoViewIfNeeded();
  const list = map.locator('.subdivision-region-list');
  await expect(list.locator('tbody tr')).toHaveCount(4);
  await expect(list.locator('tbody tr').first()).toHaveAttribute('data-region-code', 'BR-AM');
  await list.getByRole('button', { name: '排序 · 报告病例数' }).click();
  await expect(list.locator('tbody tr').first()).toHaveAttribute('data-region-code', 'BR-AC');
  await expect(list.locator('tbody tr').last()).toHaveAttribute('data-region-code', 'BR-AP');
  await list.getByRole('button', { name: /亚马孙州/ }).click();
  await expect(map.getByRole('combobox', { name: '选中地区' })).toHaveValue('BR-AM');
  await expect(list.locator('[data-region-code="BR-AM"]')).toHaveAttribute('data-selected', 'true');
  await map.getByRole('combobox', { name: '选中地区' }).selectOption('BR-AL');
  await expect(list.locator('[data-region-code="BR-AL"]')).toHaveAttribute('data-selected', 'true');
  await list.getByRole('searchbox').fill('BR-AP');
  await expect(list.locator('tbody tr')).toHaveCount(1);
  await expect(list.locator('tbody tr')).toHaveAttribute('data-region-code', 'BR-AP');
  await list.getByRole('searchbox').fill('');
  await list.getByRole('button', { name: '排序 · 变化' }).click();
  await expect(list.locator('thead th').last()).toHaveAttribute('aria-sort', 'descending');
  await map.getByRole('combobox', { name: '对比范围' }).selectOption('none');
  await list.getByRole('button', { name: '排序 · 报告病例数' }).click();
  await expect(list.locator('thead th').last()).toHaveAttribute('aria-sort', 'ascending');
  await expect(list.locator('tbody tr').first()).toHaveAttribute('data-region-code', 'BR-AC');
  await map.getByRole('combobox', { name: '对比范围' }).selectOption('previous_year');
  const legend = map.getByRole('img', { name: /地区分布/ });
  await expect(legend).toHaveAttribute('aria-label', /地区分布.*0;.*缺失／抑制/);
  await expect(map.locator('canvas')).toBeVisible();
  const visual = await map.evaluate(async element => {
    const modulePath = '/src/lib/echartsMap.ts';
    const { default: echarts } = await import(modulePath);
    const chart = echarts.getInstanceByDom(element.querySelector('[_echarts_instance_]'));
    return {
      colors: [0, 1, 2, 3].map(dataIndex => chart.getVisual({ seriesIndex: 0, dataIndex }, 'color')),
      point: chart.convertToPixel({ seriesIndex: 0 }, [-63.5, -8]),
    };
  });
  expect(new Set(visual.colors).size).toBeGreaterThanOrEqual(3);
  await map.locator('canvas').click({ position: { x: visual.point[0], y: visual.point[1] } });
  await expect(map.getByRole('combobox', { name: '选中地区' })).toHaveValue('BR-AC');
  await expect(list.locator('[data-region-code="BR-AC"]')).toHaveAttribute('data-selected', 'true');
  await map.getByRole('combobox', { name: '选中地区' }).selectOption('BR-AL');
  const screenshot = testInfo.outputPath('regional-distribution.png');
  await map.screenshot({ path: screenshot });
  await testInfo.attach('regional-distribution', { path: screenshot, contentType: 'image/png' });
  expect(await map.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  await expect(map.getByRole('link', { name: '下载数据', exact: true })).toHaveAttribute('href', '/zh/downloads/?country=BR-AL&disease=D028');
  const pngDownload = page.waitForEvent('download');
  await map.getByRole('button', { name: '下载 PNG', exact: true }).click();
  expect((await pngDownload).suggestedFilename()).toMatch(/\.png$/);
  await map.getByRole('button', { name: '分享配置', exact: true }).click();
  await expect(map.locator('.chart-share-link input')).toHaveValue(/mapRegion=BR-AL.*#regional-distribution/);
  await map.getByRole('tab', { name: '趋势', exact: true }).click();
  await expect(map.locator('.subdivision-map-trend canvas')).toBeVisible();
  await map.getByRole('tab', { name: '数据', exact: true }).click();
  await expect(map.locator('.subdivision-map-table tbody tr')).toHaveCount(4);
  await map.getByRole('tab', { name: '地图', exact: true }).click();
  const axe = await new AxeBuilder({ page }).include('#regional-distribution').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(axe.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
  await page.getByRole('button', { name: '切换到深色主题' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  const darkAxe = await new AxeBuilder({ page }).include('#regional-distribution').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(darkAxe.violations.filter(item => ['serious', 'critical'].includes(item.impact || ''))).toEqual([]);
  await map.getByRole('link', { name: '下载数据', exact: true }).click();
  await expect(page).toHaveURL(/\/zh\/downloads\/\?country=BR-AL&disease=D028$/);
});

test('regional map expands its canvas in fullscreen and restores on exit', async ({ page }) => {
  await page.setViewportSize({ ...page.viewportSize()!, height: 1400 });
  await page.goto('/countries/br/?mapDisease=D028', { waitUntil: 'domcontentloaded' });
  await page.locator('#regional-distribution').scrollIntoViewIfNeeded();
  const panel = page.locator('#regional-distribution .subdivision-map-workspace');
  const canvas = panel.locator('.subdivision-map-canvas canvas').first();
  await expect(canvas).toBeVisible();
  const normal = (await canvas.boundingBox())!;
  await panel.getByRole('button', { name: 'Enter full-screen', exact: true }).click();
  await expect.poll(() => panel.evaluate(element => document.fullscreenElement === element)).toBe(true);
  await expect.poll(async () => (await canvas.boundingBox())!.height).toBeGreaterThan(normal.height + 100);
  expect(await panel.evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  await panel.getByRole('button', { name: 'Exit full-screen', exact: true }).click();
  await expect.poll(async () => Math.abs((await canvas.boundingBox())!.height - normal.height)).toBeLessThanOrEqual(1);
});

test('China switches diseases and moves an unavailable old range into the new disease coverage', async ({ page }) => {
  test.setTimeout(60_000);
  const regions = [{ code: 'CN-BJ', name_en: 'Beijing', name_zh: '北京市' }, { code: 'CN-SH', name_en: 'Shanghai', name_zh: '上海市' }];
  const root = '/site-data/subdivision-maps/cn';
  const entries: Record<string, unknown> = {
    'index.json': { countries: [{ code: 'CN', path: `${root}/index.json` }] },
    'cn/index.json': {
      schema_version: 1, country_code: 'CN', expected_regions: 2, regions,
      boundary: { ...boundary, path: `${root}/boundary.json` }, population_path: `${root}/population.json`,
      diseases: [
        { id: 'D028', labels: { en: 'Pertussis', zh: '百日咳' }, years: [2025], cadence: 'monthly', path: `${root}/d028.json` },
        { id: 'D021', labels: { en: 'Dengue', zh: '登革热' }, years: [2024], cadence: 'monthly', path: `${root}/d021.json` },
        { id: 'D037', labels: { en: 'Measles', zh: '麻疹' }, years: [2025], cadence: 'monthly', path: `${root}/d037.json` },
      ],
    },
    'cn/boundary.json': { type: 'FeatureCollection', features: regions.map((region, index) => ({ type: 'Feature', properties: { name: region.code }, geometry: { type: 'Polygon', coordinates: [[[110 + index, 30], [111 + index, 30], [111 + index, 31], [110 + index, 31], [110 + index, 30]]] } })) },
    'cn/population.json': { schema_version: 1, country_code: 'CN', regions: Object.fromEntries(regions.map(region => [region.code, Object.fromEntries([2024, 2025].map(year => [year, { year, location_code: region.code, value: 100000, source: 'NBS', reference_date: `${year}-12-31`, basis: 'year_end_resident_population', source_url: 'https://www.stats.gov.cn/', version: 'fixture', method: 'same_calendar_year_no_interpolation' }]))])) },
  };
  for (const [id, year, title, count] of [['D028', 2025, 'Pertussis', 20], ['D021', 2024, 'Dengue', 0], ['D037', 2025, 'Measles', 5]] as const) {
    entries[`cn/${id.toLowerCase()}.json`] = {
      schema_version: 1, country_code: 'CN', disease_id: id, labels: { en: title, zh: id === 'D021' ? '登革热' : id === 'D037' ? '麻疹' : '百日咳' },
      years: [year], cadence: 'monthly', comparison_key: 'same', case_basis: {},
      regions: Object.fromEntries(regions.map(region => [region.code, { comparison_key: 'same', data_layer: 'series_registry', semantics: { metric: 'case_notifications', definition: 'v1', reporting_basis: 'notification', time_basis: 'notification_month', source_series: ['TEST'] }, points: [[`${year}-${id === 'D037' ? '12' : '02'}-01`, count, 'observed']] }])),
    };
  }
  for (const [key, value] of Object.entries(entries)) await page.route(`**/site-data/subdivision-maps/${key}`, route => route.fulfill({ json: value }));
  await page.goto('/zh/countries/cn/?mapDisease=D028&mapFrom=2025-02-01&mapTo=2025-02-28&mapMetric=rate', { waitUntil: 'domcontentloaded' });
  const panel = page.locator('#regional-distribution');
  await panel.scrollIntoViewIfNeeded();
  const disease = panel.getByRole('combobox', { name: '疾病', exact: true });
  await expect(disease).toHaveValue('D028');
  await expect(panel.locator('.subdivision-region-scroll tbody tr').first().locator('td').first()).toContainText('20');
  await disease.selectOption('D037');
  await expect(disease).toHaveValue('D037');
  await expect(panel.locator('input[type="date"]').first()).toHaveValue('2025-12-01');
  await expect(panel.locator('.subdivision-map-workspace')).toHaveAttribute('data-chart-title', /麻疹/);
  await disease.selectOption('D021');
  await expect(disease).toHaveValue('D021');
  await expect(panel.locator('input[type="date"]').first()).toHaveValue('2024-02-01');
  await expect(panel.getByRole('combobox', { name: '年份', exact: true })).toHaveValue('2024');
  await expect(panel.locator('.subdivision-map-workspace')).toHaveAttribute('data-chart-title', /登革热/);
  await expect(panel.locator('.subdivision-region-scroll tbody tr').first().locator('td').first()).toHaveText('0');
  await panel.getByRole('button', { name: '分享配置', exact: true }).click();
  const shared = new URL(await panel.locator('.chart-share-link input').inputValue());
  shared.pathname = '/zh/countries/cn/diseases/hepatitis-b/';
  await page.goto(shared.toString(), { waitUntil: 'domcontentloaded' });
  await panel.scrollIntoViewIfNeeded();
  await expect(disease).toHaveValue('D021');
  await expect(panel.locator('.subdivision-map-workspace')).toHaveAttribute('data-chart-title', /登革热/);
  await disease.selectOption('D028');
  await expect(panel.getByRole('combobox', { name: '年份', exact: true })).toHaveValue('2025');
  await expect(panel.locator('.subdivision-map-workspace')).toHaveAttribute('data-chart-title', /百日咳/);
});
