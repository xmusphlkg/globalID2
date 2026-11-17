import { expect, test, type Page } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import type { SituationAlertRecord } from '../src/utils/situationTypes';

// Cold Astro/map compilation can take most of the default 30-second budget.
test.setTimeout(60_000);

async function mockSituation(page: Page) {
  await page.route('**/astro/runtime/client/dev-toolbar/entrypoint.js', route => route.fulfill({ body: '', contentType: 'application/javascript' }));
  const alert = (id: string, published: string, country: string, extra = {}): SituationAlertRecord => ({
    alert_id: id, outbreak_id: 'OB-test', notice_id: `notice-${id}`,
    source_id: 'feed-one', source_label_en: 'Feed one', source_label_zh: null,
    agency_en: null, agency_zh: null, title_en: 'Test report', url: `https://example.org/${id}`,
    disease_id: 'D050', disease_slug: 'ebola', disease_label: 'Ebola', disease_name_en: 'Ebola', disease_name_zh: null,
    country_code: country, country_name_en: country === 'CD' ? 'DR Congo' : 'Kenya', country_name_zh: null,
    admin_area: null, status: 'ongoing', cases: 6, deaths: 0, count_basis: 'confirmed',
    as_of: published, published_date: published, quote: 'Six cases reported.',
    summary_en: null, summary_zh: null, summary_fr: null, ai: null, ...extra,
  });
  const alerts = [
    alert('fresh', '2026-11-03', 'CD'),
    alert('fresh-copy', '2026-11-02', 'CD', { source_id: 'feed-two', source_label_en: 'Feed two', as_of: '2026-11-03' }),
    alert('recent', '2026-10-20', 'KE'),
    alert('older', '2026-09-01', 'CD', { admin_area: 'Old place' }),
  ];
  await page.route('**/situation/map-data.json', route => route.fulfill({ json: {
    schema_version: 'gids.situation_alerts.v1', as_of: '2026-11-04', generated_at: '2026-11-04',
    points: alerts.map(row => row.alert_id === 'fresh-copy' ? { ...row, published_date: '2026-11-03' } : row), monitoring: [
      { notice_id: 'monitor-cd', source_id: 'feed-one', source_label_en: 'Feed one', title_en: 'Ebola monitoring in DR Congo', url: 'https://example.org/monitor-cd', published_date: '2026-11-03', alert_count: 0 },
      { notice_id: 'monitor-ke', source_id: 'monitor-only', source_label_en: 'Monitoring feed', title_en: 'Ebola monitoring in Kenya', url: 'https://example.org/monitor-ke', published_date: '2026-10-20', alert_count: 0 },
      { notice_id: 'monitor-old', source_id: 'feed-one', source_label_en: 'Feed one', title_en: 'Influenza monitoring in Kenya', url: 'https://example.org/monitor-old', published_date: '2026-09-01', alert_count: 0 },
    ], active_outbreaks: 1, sources_ok: 2, source_count: 2,
  } }));
  await page.route('**/situation/outbreaks/OB-test.json', route => route.fulfill({ json: {
    outbreak_id: 'OB-test', first_published: '2026-09-01', last_published: '2026-11-03', alert_count: alerts.length, alerts,
  } }));
}

async function openPlaceList(page: Page) {
  await expect(page.locator('.sit-item').first()).toBeAttached();
  const toggle = page.locator('.sit-bar-links button');
  if (await toggle.getAttribute('aria-pressed') === 'false') await toggle.click();
}

async function selectFilter(page: Page, name: 'Disease' | 'Area' | 'Source' | 'Time window', value: string) {
  const toggle = page.locator('.sit-bar-links button');
  if (await toggle.getAttribute('aria-pressed') === 'false') await toggle.click();
  await page.locator('.sit-list').getByRole('combobox', { name, exact: true }).click();
  await page.locator(`.sit-list [role="option"][data-value="${value}"]`).click();
}

// Inspect the actual chart series, not just React props or a canvas screenshot.
async function renderedPointIds(page: Page): Promise<string[]> {
  return page.evaluate(async () => {
    const modulePath = '/src/lib/echartsMap.ts';
    const { default: echarts } = await import(/* @vite-ignore */ modulePath);
    const node = document.querySelector('.sit-canvas .echarts-for-react');
    const chart = node && echarts.getInstanceByDom(node);
    if (!chart) return [];
    return chart.getOption().series.flatMap((series: { data: { id: string }[] }) => series.data.map(point => point.id)).sort();
  });
}

test('situation filters remove old map series, including an empty result', async ({ page }) => {
  await mockSituation(page);
  await page.goto('/situation/?window=all');
  await openPlaceList(page);
  await expect(page.locator('.sit-item')).toHaveCount(3);
  await expect.poll(() => renderedPointIds(page)).toEqual(['fresh', 'older', 'recent']);
  await selectFilter(page, 'Time window', '30');
  await expect.poll(() => renderedPointIds(page)).toEqual(['fresh', 'recent']);
  await selectFilter(page, 'Time window', '7');
  await expect.poll(() => renderedPointIds(page)).toEqual(['fresh']);
  await selectFilter(page, 'Area', 'KE');
  await expect(page.locator('.sit-item')).toHaveCount(0);
  await expect.poll(() => renderedPointIds(page)).toEqual([]);
  await page.locator('.sit-reset').click();
  await expect.poll(() => renderedPointIds(page)).toEqual(['fresh', 'recent']);
});

test('timeline shares filters, merges duplicates and keeps all report links', async ({ page }) => {
  await mockSituation(page);
  await page.goto('/situation/?window=all');
  await openPlaceList(page);
  await expect(page.locator('.sit-item')).toHaveCount(3);
  await page.locator('.sit-item').first().click();
  const rows = page.locator('.sit-alert');
  await expect(rows).toHaveCount(1);
  const first = rows.first();
  await expect(first.locator('.sit-source-link')).toHaveCount(2);
  await expect(first.locator('.sit-source-link').first()).toHaveAttribute('href', 'https://example.org/fresh');
  await expect(first.locator('.sit-source-link').last()).toHaveAttribute('href', 'https://example.org/fresh-copy');

  await expect(page.locator('.sit-filter-grid')).toHaveCount(1);
  await expect(page.locator('.sit-timeline-wrap [role="combobox"]')).toHaveCount(0);
  await selectFilter(page, 'Area', 'CD');
  await expect(rows).toHaveCount(1);
  await expect(page.locator('.sit-alert[data-country="KE"]')).toHaveCount(0);
  await selectFilter(page, 'Time window', '7');
  await expect(rows).toHaveCount(1);
  await selectFilter(page, 'Source', 'feed-two');
  await expect(rows.first()).toHaveAttribute('data-alert-id', 'fresh-copy');
  await expect(rows.locator('.sit-source-link')).toHaveCount(1);
  await expect(page).toHaveURL(/source=feed-two/);
  await selectFilter(page, 'Source', '');
  await selectFilter(page, 'Time window', 'all');
  await selectFilter(page, 'Area', 'KE');
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
  await page.locator('.sit-item').first().click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute('data-country', 'KE');
  await openPlaceList(page);
  await page.locator('.sit-reset').click();
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
  await selectFilter(page, 'Area', 'KE');
  await selectFilter(page, 'Source', 'feed-two');
  await expect(page.locator('.sit-item')).toHaveCount(0);
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
});

test('explicit time and source deep links survive reload with a selected place', async ({ page }) => {
  await mockSituation(page);
  await page.goto('/situation/?place=fresh-copy&source=feed-two&window=7');
  await expect(page.locator('.sit-item')).toHaveCount(1);
  await expect(page.locator('.sit-alert')).toHaveCount(1);
  await openPlaceList(page);
  await expect(page.locator('.sit-list').getByRole('combobox', { name: 'Source', exact: true })).toHaveValue('Feed two');
  await page.reload();
  await expect(page.locator('.sit-alert')).toHaveCount(1);
  await openPlaceList(page);
  await expect(page.locator('.sit-list').getByRole('combobox', { name: 'Time window', exact: true })).toHaveValue('Last 7 days');
});

test('lower-level area deep links wait for the current catalogue and retain its own series', async ({ page }) => {
  await mockSituation(page);
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', async route => {
    await new Promise(resolve => setTimeout(resolve, 200));
    await route.fulfill({ json: { countries: [{ code: 'CN-BJ', name_en: 'Beijing', name_zh: '北京市', location_type: 'subdivision', source_info: { parent_country_code: 'CN' }, disease_count: 1 }] } });
  });
  await page.route('**/site-data/diseases/d038-summary.json', route => route.fulfill({ json: { reporting_countries: [{ code: 'CN-BJ', cases_tail: [2, 5], dates_tail: ['2026-09-01', '2026-10-01'] }] } }));
  await page.goto('/situation/?country=CN-BJ&disease=D038&window=all');
  await expect(page.locator('.sit-detail.is-open')).toContainText('Beijing');
  await expect(page.locator('.sit-detail.is-open .sit-series-main strong')).toHaveText('5');
  expect(new URL(page.url()).searchParams.get('country')).toBe('CN-BJ');
  await page.reload();
  await expect(page.locator('.sit-detail.is-open')).toContainText('Beijing');
  expect(new URL(page.url()).searchParams.get('country')).toBe('CN-BJ');
  await page.goto('/situation/?country=CN-UNKNOWN&window=all');
  await expect(page.locator('.sit-item')).toHaveCount(3);
  expect(new URL(page.url()).searchParams.has('country')).toBe(false);
});

test('situation page: select a place, expand timeline cards, graph stays attached', async ({ page }, testInfo) => {
  await mockSituation(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/situation/');
  await openPlaceList(page);
  const items = page.locator('.sit-item');
  await expect(items.first()).toBeVisible({ timeout: 30_000 });

  await items.first().click();
  const detail = page.locator('.sit-detail.is-open');
  await expect(detail).toBeVisible();

  const alerts = detail.locator('.sit-alert');
  await expect(alerts.first()).toBeVisible({ timeout: 15_000 });

  const rows = Math.min(await alerts.count(), 4);
  for (let index = 0; index < rows; index += 1) {
    const row = alerts.nth(index);
    const summary = row.locator('summary');
    if (!(await row.locator('details').evaluate(node => (node as HTMLDetailsElement).open))) await summary.click();
    const box = await row.boundingBox();
    const graph = await row.locator('.sit-graph').boundingBox();
    expect(box).not.toBeNull();
    expect(graph).not.toBeNull();
    // The graph column must span the whole expanded row, with no 150px SVG padding either.
    expect(Math.abs(graph!.height - box!.height)).toBeLessThan(2);
  }

  const first = await alerts.first().boundingBox();
  expect(first!.height).toBeLessThan(700);
  await detail.screenshot({ path: testInfo.outputPath('timeline.png') });
  expect(errors).toEqual([]);
});

test('situation page: filtering recentres the map without errors', async ({ page }) => {
  await mockSituation(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/situation/');
  await openPlaceList(page);
  await expect(page.locator('.sit-item').first()).toBeVisible({ timeout: 30_000 });
  await selectFilter(page, 'Area', 'CD');
  await expect(page.locator('.sit-canvas canvas').first()).toBeVisible();
  await page.locator('.sit-reset').click();
  expect(errors).toEqual([]);
});


test('typing filters keeps place selection explicit, scopes monitoring cards and restores cancelled searches', async ({ page }) => {
  await mockSituation(page);
  await page.goto('/situation/');
  await openPlaceList(page);
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
  const disease = page.getByRole('combobox', { name: 'Disease', exact: true });
  await disease.fill('d050');
  await expect(page.getByRole('option', { name: /Ebola/ })).toBeVisible();
  await disease.press('Enter');
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
  expect(new URL(page.url()).searchParams.has('place')).toBe(false);
  await page.locator('.sit-item').first().click();
  await expect(page.locator('.sit-alert')).toHaveCount(1);
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(1);
  await openPlaceList(page);
  await expect(page.locator('.sit-monitor-count')).toHaveText('2');
  await selectFilter(page, 'Area', 'KE');
  await expect(page.locator('.sit-monitor-card')).toHaveCount(1);
  await expect(page.locator('.sit-monitor-card')).toContainText('Ebola monitoring in Kenya');
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
  await page.locator('.sit-item').first().click();
  await openPlaceList(page);
  await expect(page.locator('.sit-alert')).toHaveCount(1);
  await disease.fill('unmatched query');
  await expect(page.getByRole('option')).toHaveCount(0);
  await disease.press('Escape');
  await expect(disease).toHaveValue('Ebola');
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(1);
  await selectFilter(page, 'Source', 'feed-one');
  await expect(page.locator('.sit-monitor-card')).toHaveCount(0);
  await expect(page.locator('.sit-monitor-products')).toContainText('No monitoring products match');
  await page.locator('.sit-reset').click();
  await expect(page.locator('.sit-monitor-count')).toHaveText('2');
  await selectFilter(page, 'Source', 'monitor-only');
  await expect(page.locator('.sit-monitor-card')).toContainText('Ebola monitoring in Kenya');
  await page.reload();
  await expect(page.locator('.sit-monitor-card')).toContainText('Ebola monitoring in Kenya');
  await expect(page).toHaveURL(/source=monitor-only/);
  await selectFilter(page, 'Source', '');
  await selectFilter(page, 'Time window', 'all');
  await selectFilter(page, 'Disease', 'D038');
  await expect(page.locator('.sit-monitor-card')).toHaveCount(1);
  await expect(page.locator('.sit-monitor-card')).toContainText('Influenza monitoring in Kenya');
});

test('MERS shared link groups identical cumulative figures across channels and dates', async ({ page }, testInfo) => {
  // Freeze the six public reports from the shared example so daily exports cannot change this regression.
  const { map, outbreak } = JSON.parse(await readFile(new URL('./fixtures/mers-sa.json', import.meta.url), 'utf8'));
  await page.route('**/situation/map-data.json', route => route.fulfill({ json: map }));
  await page.route('**/situation/outbreaks/OB-8c592ec14dae.json', route => route.fulfill({ json: outbreak }));
  await page.goto('/situation/?disease=D049&window=90&place=e0ed903256a8d578');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const rows = page.locator('.sit-alert');
  await expect(rows).toHaveCount(2);
  await expect(page.locator('.sit-alert.is-selected')).toHaveCount(1);
  await expect(rows.first().locator('.sit-source-link')).toHaveCount(3);
  await expect(rows.last().locator('.sit-source-link')).toHaveCount(3);
  // This historical fixture predates explicit host/unit metadata. Its counts
  // remain grouped, while the current UI must disclose the unspecified unit.
  await expect(rows.first().locator('.sit-alert-counts')).toHaveText('8 reported cases (unit unspecified) · 1 reported deaths (unit unspecified)');
  await expect(rows.last().locator('.sit-alert-counts')).toHaveText('2 reported cases (unit unspecified) · 1 reported deaths (unit unspecified)');
  await expect(page.getByText('Shading: latest GIDS value for the selected disease.')).toHaveCount(0);
  const legend = page.locator('.sit-legend-float');
  if (await legend.isVisible()) expect(await legend.locator('ul').evaluate(node => getComputedStyle(node).overflow)).toBe('visible');
  await page.screenshot({ path: testInfo.outputPath('mers-grouped.png') });
});

for (const prefix of ['', '/zh', '/fr']) {
  test(`disease-only links never select a place on load or reload in ${prefix || 'en'}`, async ({ page }) => {
    await mockSituation(page);
    const outbreakRequests: string[] = [];
    page.on('request', request => { if (request.url().includes('/outbreaks/')) outbreakRequests.push(request.url()); });
    await page.goto(`${prefix}/situation/?disease=D050&window=all`, { waitUntil: 'domcontentloaded' });
    await openPlaceList(page);
    await expect(page.locator('.sit-item')).toHaveCount(3);
    await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
    expect(new URL(page.url()).searchParams.has('place')).toBe(false);
    expect(new URL(page.url()).searchParams.has('country')).toBe(false);
    expect(outbreakRequests).toEqual([]);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await openPlaceList(page);
    await expect(page.locator('.sit-item')).toHaveCount(3);
    await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
    expect(new URL(page.url()).searchParams.has('place')).toBe(false);
    await page.locator('.sit-item').first().click();
    await expect(page.locator('.sit-detail.is-open')).toHaveCount(1);
    await expect.poll(() => new URL(page.url()).searchParams.has('place')).toBe(true);
  });
}
