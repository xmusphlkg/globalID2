import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';

const hash = (body: string | Buffer) => createHash('sha256').update(body).digest('hex');
const source = { label: 'Official weekly source', url: 'https://example.test/authority', cadence: 'weekly', description: 'Published weekly case notifications.' };
const country = { code: 'JP', name_en: 'Japan', name_zh: '日本', data_available: true, record_count: 12, disease_count: 1, total_cases: 30, date_range: { start: '2026-10-04', end: '2026-10-11' }, source_info: { sources: [source] } };
const series = { disease_id: 'D038', slug: 'influenza', name_en: 'Influenza', name_zh: '流感', dates: ['2026-10-04', '2026-10-11'], cases: [10, 0], total_cases: 10, period_granularity: 'weekly', comparability: 'comparable', date_range: country.date_range };
function bundle(entries: Record<string, unknown>, version: string, root: string) {
  const objects = new Map<string, string>();
  const files = Object.fromEntries(Object.entries(entries).map(([key, value]) => {
    const body = JSON.stringify(value), sha256 = hash(body);
    objects.set(sha256, body);
    return [key, { url: `${root}/objects/${sha256}.json`, sha256, bytes: Buffer.byteLength(body) }];
  }));
  return { manifest: { schema_version: 1, release_id: version, files }, objects };
}
function surveillance(version = 1) {
  return bundle({
    'meta.json': { generated_at: '2026-11-07', total_countries: 2, total_diseases: 1, countries: [country, { ...country, code: 'JP-13', parent_code: 'JP', location_type: 'subdivision' }] },
    'site-status.json': { generated_at: '2026-11-07' },
    'home-influenza.json': { schema_version: 1, series: { JP: { cases: [1, 8 * version, 3] }, CN: { cases: [3, 1, 8] } } },
    'countries/jp-summary.json': { ...country, country_code: 'JP', country_name_en: 'Japan', total_deaths: 0, generated_at: '2026-11-07', diseases: [series], source_info: { sources: [source, source] }, country_brief_updated_at: '2026-10-10' },
    'countries/jp.json': { country_code: 'JP', disease_series: { D038: series } },
    'countries/jp-source-series.json': { v: 1, series: {} },
    'diseases/d038-summary.json': { ...series, generated_at: '2026-11-07', country_count: 1, source_info: [{ country_code: 'JP', sources: [source, source] }], reporting_countries: [{ ...country, dates_tail: series.dates, cases_tail: series.cases, period_granularity: 'weekly', comparability: 'comparable' }] },
    'diseases/d038.json': { disease_id: 'D038', country_series: { JP: { ...series, name_en: 'Japan', name_zh: '日本' } } },
  }, `restored-${version}`, '/site-data');
}
const schema_version = 'gids.situation_alerts.v1';
const outbreak = { outbreak_id: 'OB-restored', disease_id: 'D038', disease_name_en: 'Influenza', disease_name_zh: '流感', disease_label: 'Influenza', country_codes: ['JP'], first_published: '2026-11-02' };
const alert = { ...outbreak, alert_id: 'restored-alert', country_code: 'JP', country_name_en: 'Japan', country_name_zh: '日本', published_date: '2026-11-05', status: 'ongoing', source_id: 'official', source_label_en: 'Official source', url: 'https://example.test/notice', summary_en: 'Reviewed update', cases: 10 };
const week = { schema_version, period_key: '2026-W45', new_outbreaks: ['OB-restored'], updated_outbreaks: [], new_places: 1, alerts: 1, outbreaks: [outbreak] };
const situation = bundle({
  'weekly/index.json': { schema_version, periods: [week, { ...week, period_key: '2026-W44', new_outbreaks: [], updated_outbreaks: ['OB-restored'] }] },
  'weekly/2026-W45.json': week,
  'outbreaks/OB-restored.json': { schema_version, ...outbreak, alerts: [alert] },
  'by-country/JP.json': { schema_version, alerts: [alert] },
  'by-disease/D038.json': { schema_version, alerts: [alert] },
  'map-data.json': { schema_version, as_of: '2026-11-07', points: [alert], active_outbreaks: 1, sources_ok: 1 },
}, 'restored-situation', '/site-data/situation');
async function mock(page: Page) {
  let current = surveillance();
  const objects = new Map([...current.objects, ...surveillance(2).objects, ...situation.objects]);
  const requested: string[] = [];
  await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/situation/current.json', route => route.fulfill({ json: situation.manifest }));
  await page.route(/\/site-data\/(?:situation\/)?objects\/.*\.json$/, route => {
    const url = new URL(route.request().url());
    requested.push(url.pathname);
    const key = url.pathname.split('/').at(-1)!.replace('.json', '');
    return objects.has(key) ? route.fulfill({ body: objects.get(key), contentType: 'application/json' }) : route.fulfill({ status: 404 });
  });
  await page.route('**/site-data/downloads/**/*.json', route => route.fulfill({ json: { schema_version: 1, generated_at: '2026-10-01', record_count: 12, parts: [{ id: 'current', label: '2026', date_range: country.date_range, files: { csv: { url: 'https://example.test/jp.csv', bytes: 1000 }, json: { url: 'https://example.test/jp.json' }, xlsx: { url: 'https://example.test/jp.xlsx' } } }] } }));
  return { update: () => { current = surveillance(2); }, requested };
}

test('home restores real curves and clear national coverage without fetching country histories', async ({ page, request }) => {
  const data = await mock(page);
  const before = hash(await (await request.get('/zh/')).body());
  await page.goto('/zh/');
  await expect(page.locator('.curve-field polyline')).toHaveCount(2);
  await expect(page.locator('.curve-annotation')).toContainText('日本流感 · 中国流感');
  await expect(page.locator('.data-console')).toContainText('国家级辖区');
  await expect(page.locator('.data-console')).toContainText('省级／地区数据集');
  await expect(page.locator('.home-recent')).toContainText('每周');
  const first = await page.locator('[data-country="JP"]').getAttribute('points');
  const full = surveillance().manifest.files['countries/jp.json'].url;
  expect(data.requested).not.toContain(full);
  data.update();
  expect(await page.locator('[data-country="JP"]').getAttribute('points')).toBe(first);
  await page.reload();
  await expect(page.locator('.curve-field')).toHaveAttribute('data-surveillance-version', 'restored-2');
  expect(await page.locator('[data-country="JP"]').getAttribute('points')).not.toBe(first);
  expect(hash(await (await request.get('/zh/')).body())).toBe(before);
});

test('country restores contextual links, source cadence and separately dated download formats', async ({ page }) => {
  await mock(page);
  await page.goto('/zh/countries/jp/');
  await expect(page.locator('#disease-directory a').first()).toHaveAttribute('href', '/zh/countries/jp/diseases/influenza/');
  await page.locator('#downloads').scrollIntoViewIfNeeded();
  await expect(page.getByTestId('download-archive')).toContainText('2026-10-01');
  await expect(page.locator('#downloads')).toContainText('每周');
  await expect(page.locator('#downloads [data-testid="surveillance-sources"] article')).toHaveCount(1);
  await expect(page.locator('#downloads a[href$=".csv"]')).toHaveCount(1);
  await page.getByLabel('文件格式', { exact: false }).selectOption('xlsx');
  await expect(page.locator('#downloads a[href$=".csv"]')).toHaveCount(0);
  await expect(page.locator('#downloads a[href$=".xlsx"]')).toHaveCount(1);
  await page.locator('#international').scrollIntoViewIfNeeded();
  await expect(page.locator('.intl-panel-snapshot')).toContainText('本地快照');
  await expect(page.locator('.intl-panel-snapshot')).toContainText('-100%');
});

test('disease page keeps the selected disease when opening a country', async ({ page }) => {
  await mock(page);
  await page.goto('/zh/diseases/influenza/');
  await expect(page.locator('#coverage a[href="/zh/countries/jp/diseases/influenza/"]')).toHaveCount(1);
});

test('French country facts and disease names retain localization', async ({ page }) => {
  await mock(page);
  await page.goto('/fr/countries/jp/');
  await expect(page.locator('#disease-directory')).toContainText('Grippe');
  await page.locator('#sources summary').click();
  await expect(page.locator('#sources')).toContainText('Hebdomadaire');
  await expect(page.locator('#sources')).toContainText('Sources officielles');
  await expect(page.locator('#sources')).not.toContainText('The dataset is built');
});

test('directory restores subdivision entry and reset without losing filters', async ({ page }) => {
  await mock(page);
  await page.goto('/zh/countries/?q=Japan');
  await expect(page.locator('a[href="/zh/countries/jp/#subnational-data"]')).toHaveCount(1);
  await page.getByRole('button', { name: '重置筛选', exact: true }).click();
  await expect(page.getByPlaceholder('搜索名称或代码')).toHaveValue('');
  await expect(page).not.toHaveURL(/q=/);
});

test('archives show named groups and adjacent periods while timelines stay lazy', async ({ page }) => {
  const data = await mock(page);
  await page.goto('/zh/situation/weekly/?period=2026-W45');
  await expect(page.getByRole('heading', { name: '新增疫情 · 1' })).toBeVisible();
  const summary = page.locator('main details summary').filter({ hasText: '流感' });
  await expect(summary).toContainText('日本');
  expect(data.requested).not.toContain(situation.manifest.files['outbreaks/OB-restored.json'].url);
  await summary.click();
  await expect(page.locator('main details')).toContainText('Reviewed update');
  await expect(page.locator('main nav a').filter({ hasText: '2026-W44' })).toHaveCount(1);
  await page.goto('/zh/situation/monthly/?month=2026-11');
  await expect(page.locator('main')).toContainText('本月');
  await expect(page.getByRole('link', { name: '全部月份', exact: true })).toBeVisible();
});
