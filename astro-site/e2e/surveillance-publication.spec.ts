import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';

const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
const publication = (version: number) => {
  const year = version === 1 ? 2025 : 2026;
  const cases = version === 1 ? [10, 20, 30, 40, 50, 60] : [15, 25, 35, 45, 55, 160];
  const dates = cases.map((_, month) => `${year}-${String(month + 1).padStart(2, '0')}-01`);
  const reporting = [{ code: 'JP', name_en: 'Japan' }, ...(version === 2 ? [{ code: 'US', name_en: 'United States' }] : [])];
  const summary = { country_count: reporting.length, date_range: { start: dates[0], end: dates.at(-1) }, reporting_countries: reporting.map(row => ({ ...row, cases_tail: cases, dates_tail: dates })), generated_at: `${year}-07-01` };
  const curve = { country_series: { JP: { disease_id: 'D038', name_en: 'Japan', name_zh: '日本', dates, cases, weekly_equiv_cases: [], deaths: cases.map(() => 0), incidence_rates: cases.map(() => null), total_cases: cases.reduce((sum, value) => sum + value, 0), period_granularity: 'monthly' } } };
  if (version === 2) (curve.country_series as any).US = { ...curve.country_series.JP, name_en: 'United States', name_zh: '美国' };
  const objects = new Map<string, string>();
  const files: Record<string, { url: string; sha256: string; bytes: number }> = {};
  // Clean-build CI calls its fixture influenza D001; the reviewed catalogue uses D038.
  for (const id of ['d001', 'd038']) for (const [name, value] of [[`diseases/${id}-summary.json`, summary], [`diseases/${id}.json`, curve]] as const) {
    const body = JSON.stringify(value); const sha256 = hash(body);
    objects.set(sha256, body); files[name] = { url: `objects/${sha256}.json`, sha256, bytes: Buffer.byteLength(body) };
  }
  const countrySeries = { ...curve.country_series.JP, name_en: 'Influenza', name_zh: '流行性感冒', slug: 'influenza', category: 'Viral', latest_cases: cases.at(-1) };
  const countrySummary = { country_code: 'JP', generated_at: `${year}-07-01`, total_cases: countrySeries.total_cases, total_deaths: 0, disease_count: 1, date_range: summary.date_range, diseases: [countrySeries] };
  for (const [name, value] of [['countries/jp-summary.json', countrySummary], ['countries/jp.json', { country_code: 'JP', disease_series: { D038: countrySeries } }]] as const) {
    const body = JSON.stringify(value); const sha256 = hash(body);
    objects.set(sha256, body); files[name] = { url: `objects/${sha256}.json`, sha256, bytes: Buffer.byteLength(body) };
  }
  for (const [name, value] of [['site-status.json', { generated_at: `${year}-07-01` }], ['meta.json', { generated_at: `${year}-07-01`, countries: reporting, disease_coverage: { D001: { country_count: reporting.length }, D038: { country_count: reporting.length } } }]] as const) {
    const body = JSON.stringify(value); const sha256 = hash(body);
    objects.set(sha256, body); files[name] = { url: `objects/${sha256}.json`, sha256, bytes: Buffer.byteLength(body) };
  }
  return { manifest: { schema_version: 1, release_id: `browser-${version}`, files }, objects };
};

test('JSON publication updates summary and chart without changing static HTML, and pins lazy readers', async ({ page, request }) => {
  const first = publication(1); const second = publication(2);
  let current = first;
  const objects = new Map([...first.objects, ...second.objects]);
  await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/objects/*.json', route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!.replace('.json', '');
    return objects.has(key) ? route.fulfill({ body: objects.get(key), contentType: 'application/json' }) : route.fulfill({ status: 404 });
  });
  const before = hash(await (await request.get('/diseases/influenza/')).body());
  await page.goto('/diseases/influenza/', { waitUntil: 'networkidle' });
  await expect(page.locator('[data-surveillance-version="browser-1"]').first()).toBeVisible();
  await expect(page.locator('#overview')).toContainText('2025-06-01');
  await expect(page.locator('[data-database-updated]')).toContainText('2025');
  current = second;
  await page.locator('#main-trend').scrollIntoViewIfNeeded();
  const explorer = page.getByTestId('surveillance-explorer').first();
  await expect(explorer).toBeVisible();
  await explorer.getByRole('button', { name: 'All data', exact: true }).click();
  await explorer.getByRole('tab', { name: 'Data', exact: true }).click();
  await expect(explorer.getByTestId('surveillance-data')).toContainText('2025-06-01');
  await expect(page.locator('[data-surveillance-version="browser-2"]')).toHaveCount(0);
  await expect(page.locator('[data-database-updated]')).toContainText('2025');
  await page.goto('/diseases/influenza/', { waitUntil: 'networkidle' });
  await expect(page.locator('[data-surveillance-version="browser-2"]').first()).toBeVisible();
  await expect(page.locator('#overview')).toContainText('2026-06-01');
  await expect(page.locator('[data-database-updated]')).toContainText('2026');
  await page.locator('#main-trend').scrollIntoViewIfNeeded();
  await explorer.getByRole('button', { name: 'All data', exact: true }).click();
  await explorer.getByRole('tab', { name: 'Data', exact: true }).click();
  await expect(explorer.getByTestId('surveillance-data')).toContainText('160');
  expect(hash(await (await request.get('/diseases/influenza/')).body())).toBe(before);
});

test('country indicators and lazy curves update together while the template stays unchanged', async ({ page, request }) => {
  let current = publication(1);
  const objects = new Map([...current.objects, ...publication(2).objects]);
  await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/objects/*.json', route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!.replace('.json', '');
    return route.fulfill({ body: objects.get(key), contentType: 'application/json' });
  });
  const before = hash(await (await request.get('/countries/jp/')).body());
  await page.goto('/countries/jp/', { waitUntil: 'networkidle' });
  await expect(page.locator('#indicators')).toContainText('210');
  await expect(page.locator('#overview')).toContainText('2025-06-01');
  current = publication(2);
  await page.goto('/countries/jp/', { waitUntil: 'networkidle' });
  await expect(page.locator('#indicators')).toContainText('335');
  await expect(page.locator('#overview')).toContainText('2026-06-01');
  await page.locator('#trends').scrollIntoViewIfNeeded();
  const explorer = page.getByTestId('surveillance-explorer').first();
  await expect(explorer).toBeVisible();
  await explorer.getByRole('button', { name: 'All data', exact: true }).click();
  await explorer.getByRole('tab', { name: 'Data', exact: true }).click();
  await expect(explorer.getByTestId('surveillance-data')).toContainText('160');
  expect(hash(await (await request.get('/countries/jp/')).body())).toBe(before);
});

test('comparison disease choices and country counts follow current data without download-archive rebuilds', async ({ page }) => {
  let current = publication(1);
  const objects = new Map([...current.objects, ...publication(2).objects]);
  await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/objects/*.json', route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!.replace('.json', '');
    return route.fulfill({ body: objects.get(key), contentType: 'application/json' });
  });
  await page.goto('/compare/', { waitUntil: 'networkidle' });
  const panel = page.locator('.compare-filter-panel').first();
  await expect(panel).toContainText('1 reporting countries');
  current = publication(2);
  await page.goto('/compare/', { waitUntil: 'networkidle' });
  await expect(panel).toContainText('2 reporting countries');
  await expect(page.locator('.compare-filter-panel').nth(1)).toContainText('United States');
});

test('a corrupt immutable object fails closed without displaying an older summary', async ({ page }) => {
  const current = publication(1);
  await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/objects/*.json', route => route.fulfill({ body: '{}', contentType: 'application/json' }));
  await page.goto('/diseases/influenza/');
  await expect(page.getByText('Current data is unavailable.', { exact: false }).first()).toBeVisible();
  await expect(page.locator('#overview')).not.toContainText('2025-06-01');
});

test('situation surveillance background uses the current lightweight summary instead of embedded observations', async ({ page }) => {
  let current = publication(1);
  const objects = new Map([...current.objects, ...publication(2).objects]);
  await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/objects/*.json', route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!.replace('.json', '');
    return route.fulfill({ body: objects.get(key), contentType: 'application/json' });
  });
  await page.route('**/situation/map-data.json', route => route.fulfill({ json: {
    schema_version: 'gids.situation_alerts.v1', as_of: '2026-11-07', generated_at: '2026-11-07',
    points: [{ alert_id: 'publication-fixture', outbreak_id: 'fixture', notice_id: 'fixture', source_id: 'test', source_label_en: 'Reviewed source', title_en: 'Influenza notice', url: 'https://example.test/notice', disease_id: 'D038', disease_label: 'Influenza', disease_name_en: 'Influenza', country_code: 'JP', country_name_en: 'Japan', status: 'ongoing', published_date: '2026-11-07', as_of: '2026-11-07', cases: 1, deaths: 0, count_basis: 'confirmed', quote: 'Reviewed notice.' }],
    monitoring: [], active_outbreaks: 1, sources_ok: 1, source_count: 1,
  } }));
  await page.goto('/situation/?country=JP&disease=D038&window=all', { waitUntil: 'networkidle' });
  await expect(page.locator('.sit-series-main strong').first()).toHaveText('60');
  current = publication(2);
  await page.goto('/situation/?country=JP&disease=D038&window=all', { waitUntil: 'networkidle' });
  await expect(page.locator('.sit-series-main strong').first()).toHaveText('160');
});
