import { expect, test, type Page } from '@playwright/test';
import type { SituationAlertRecord } from '../src/utils/situationTypes';

// Cold Astro/map compilation can take most of the default 30-second budget.
test.setTimeout(60_000);

async function mockSituation(page: Page) {
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
    alert('fresh-copy', '2026-11-03', 'CD', { source_id: 'feed-two', source_label_en: 'Feed two' }),
    alert('recent', '2026-10-20', 'KE'),
    alert('older', '2026-09-01', 'CD', { admin_area: 'Old place' }),
  ];
  await page.route('**/situation/map-data.json', route => route.fulfill({ json: {
    schema_version: 'gids.situation_alerts.v1', as_of: '2026-11-04', generated_at: '2026-11-04',
    points: alerts, monitoring: [], active_outbreaks: 1, sources_ok: 2, source_count: 2,
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
  const filters = page.locator('.sit-list .sit-filter-grid select');
  await filters.nth(3).selectOption('30');
  await expect.poll(() => renderedPointIds(page)).toEqual(['fresh', 'recent']);
  await filters.nth(3).selectOption('7');
  await expect.poll(() => renderedPointIds(page)).toEqual(['fresh']);
  await filters.nth(1).selectOption('KE');
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
  await expect(rows).toHaveCount(3);
  const first = rows.first();
  await expect(first.locator('.sit-source-link')).toHaveCount(2);
  await expect(first.locator('.sit-source-link').first()).toHaveAttribute('href', 'https://example.org/fresh');
  await expect(first.locator('.sit-source-link').last()).toHaveAttribute('href', 'https://example.org/fresh-copy');

  const left = page.locator('.sit-list .sit-filter-grid select');
  const right = page.locator('.sit-timeline-wrap .sit-filter-grid select');
  await right.nth(1).selectOption('CD');
  await expect(left.nth(1)).toHaveValue('CD');
  await expect(rows).toHaveCount(2);
  await expect(page.locator('.sit-alert[data-country="KE"]')).toHaveCount(0);
  await right.nth(3).selectOption('7');
  await expect(left.nth(3)).toHaveValue('7');
  await expect(rows).toHaveCount(1);
  await right.nth(2).selectOption('feed-two');
  await expect(left.nth(2)).toHaveValue('feed-two');
  await expect(rows.first()).toHaveAttribute('data-alert-id', 'fresh-copy');
  await expect(rows.locator('.sit-source-link')).toHaveCount(1);
  await expect(page).toHaveURL(/source=feed-two/);

  // Re-open the place list on narrow screens to exercise the opposite direction.
  if ((page.viewportSize()?.width ?? 0) < 900) await page.locator('.sit-bar-links button').click();
  await left.nth(2).selectOption('');
  await left.nth(3).selectOption('all');
  await left.nth(1).selectOption('KE');
  await expect(right.nth(1)).toHaveValue('KE');
  await expect(right.nth(3)).toHaveValue('all');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute('data-country', 'KE');
  await page.locator('.sit-reset').click();
  await expect(right.nth(1)).toHaveValue('');
  await expect(right.nth(3)).toHaveValue('30');
  await expect(rows).toHaveCount(2);
  await left.nth(1).selectOption('KE');
  await left.nth(2).selectOption('feed-two');
  await expect(page.locator('.sit-item')).toHaveCount(0);
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(0);
});

test('explicit time and source deep links survive reload with a selected place', async ({ page }) => {
  await mockSituation(page);
  await page.goto('/situation/?place=fresh-copy&source=feed-two&window=7');
  await expect(page.locator('.sit-item')).toHaveCount(1);
  await expect(page.locator('.sit-alert')).toHaveCount(1);
  await expect(page.locator('.sit-timeline-wrap select').nth(2)).toHaveValue('feed-two');
  await page.reload();
  await expect(page.locator('.sit-alert')).toHaveCount(1);
  await expect(page.locator('.sit-timeline-wrap select').nth(3)).toHaveValue('7');
});

test('situation page: select a place, expand timeline cards, graph stays attached', async ({ page }, testInfo) => {
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
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/situation/');
  await openPlaceList(page);
  await expect(page.locator('.sit-item').first()).toBeVisible({ timeout: 30_000 });
  const country = page.locator('.sit-filter-grid select').nth(1);
  const options = await country.locator('option').allTextContents();
  test.skip(options.length < 2, 'no country facets in the local data');
  await country.selectOption({ index: 1 });
  await expect(page.locator('.sit-canvas canvas').first()).toBeVisible();
  await page.getByRole('button', { name: /重置|Reset|Réinitialiser/ }).click();
  expect(errors).toEqual([]);
});
