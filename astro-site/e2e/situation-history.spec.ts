import { expect, test, type Page, type Route } from '@playwright/test';
import type { MonitoringProduct, SituationAlertRecord } from '../src/utils/situationTypes';

test.setTimeout(60_000);

const AS_OF = '2026-11-15';

function report(id: string, extra: Partial<SituationAlertRecord> = {}): SituationAlertRecord {
  return {
    alert_id: id, outbreak_id: 'OB-history-a', notice_id: `notice-${id}`,
    source_id: 'latest-feed', source_label_en: 'Latest feed', source_label_zh: null,
    agency_en: null, agency_zh: null, title_en: 'MERS situation report', url: `https://example.test/${id}`,
    disease_id: 'D049', disease_slug: 'mers', disease_label: 'MERS', disease_name_en: 'MERS', disease_name_zh: '中东呼吸综合征',
    country_code: 'SA', country_name_en: 'Saudi Arabia', country_name_zh: '沙特阿拉伯',
    admin_area: null, status: 'ongoing', cases: 8, deaths: 1, count_basis: 'confirmed',
    as_of: '2026-11-13', published_date: '2026-11-14',
    quote: 'Since the beginning of 2026, eight MERS cases including one death have been reported in Saudi Arabia.',
    summary_en: 'Saudi Arabia reported eight MERS cases, including one death.', summary_zh: null, summary_fr: null, ai: null,
    reporting_period: { kind: 'cumulative', start: '2026-01-01', end: '2026-11-13' },
    ...extra,
  };
}

async function mockHistory(page: Page, longCards = false) {
  const entityRequests: string[] = [];
  const outbreakRequests: string[] = [];
  await page.route('**/astro/runtime/client/dev-toolbar/entrypoint.js', route => route.fulfill({ body: '', contentType: 'application/javascript' }));
  // Keep these checks independent of generated local surveillance or manifest files.
  await page.route('**/site-data/**', route => route.fulfill({ status: 404 }));
  const alerts = [
    report('history-latest', longCards ? { summary_en: 'Saudi Arabia reported eight MERS cases, including one death. The report provides additional surveillance context for this location. '.repeat(12) } : {}),
    report('history-older', { source_id: 'older-feed', source_label_en: 'Older channel', published_date: '2026-11-02', as_of: '2026-11-01', reporting_period: { kind: 'cumulative', start: '2026-01-01', end: '2026-11-01' } }),
    report('history-second', { outbreak_id: 'OB-history-b', country_code: 'KE', country_name_en: 'Kenya', country_name_zh: '肯尼亚', cases: 3, deaths: 0, published_date: '2026-11-12', quote: 'Since the beginning of 2026, Kenya reported three MERS cases and no deaths.' }),
  ];
  const monitoring: MonitoringProduct[] = [
    { notice_id: 'roundup-mers', source_id: 'monitor-feed', source_label_en: 'Monitoring feed', source_label_zh: null, agency_en: null, agency_zh: null, title_en: 'Weekly public health roundup', url: 'https://example.test/roundup-mers', published_date: '2026-11-14', alert_count: 0, disease_ids: ['D049'], country_codes: ['SA'] },
    { notice_id: 'roundup-other', source_id: 'monitor-feed', source_label_en: 'Monitoring feed', source_label_zh: null, agency_en: null, agency_zh: null, title_en: 'Another weekly public health roundup', url: 'https://example.test/roundup-other', published_date: '2026-11-14', alert_count: 0, disease_ids: ['D038'], country_codes: ['KE'] },
  ];
  if (longCards) {
    for (let index = 0; index < 12; index += 1) monitoring.push({ ...monitoring[0], notice_id: `roundup-long-${index}`, title_en: `Weekly surveillance roundup with additional context ${index + 1}`, url: `https://example.test/roundup-long-${index}` });
  }
  await page.route('**/situation/map-data.json', route => route.fulfill({ json: {
    schema_version: 'gids.situation_alerts.v1', as_of: AS_OF, generated_at: AS_OF,
    points: [alerts[0], alerts[2]], filter_points: alerts,
    monitoring, active_outbreaks: 2, sources_ok: 3, source_count: 3,
  } }));
  await page.route('**/situation/outbreaks/*.json', route => {
    outbreakRequests.push(new URL(route.request().url()).pathname);
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1)!.replace('.json', ''));
    return route.fulfill({ json: { schema_version: 'gids.situation_alerts.v1', outbreak_id: id, as_of: AS_OF, first_published: '2026-11-02', last_published: '2026-11-14', alerts: alerts.filter(alert => alert.outbreak_id === id) } });
  });
  // Legacy exports provide outbreak shards, not root entity routes. A missing
  // manifest must select those available shards before issuing any entity read.
  const missingEntity = (route: Route) => {
    entityRequests.push(new URL(route.request().url()).pathname);
    return route.fulfill({ status: 404 });
  };
  await page.route('**/situation/by-disease/*.json', missingEntity);
  await page.route('**/situation/by-country/*.json', missingEntity);
  return { entityRequests, outbreakRequests };
}

async function showFilters(page: Page) {
  await expect(page.locator('.sit-filter-grid')).toBeAttached();
  const toggle = page.locator('.sit-bar-links button');
  if (await toggle.getAttribute('aria-pressed') === 'false') await toggle.click();
  await expect(page.locator('.sit-list')).toBeVisible();
}

async function selectFilter(page: Page, name: 'Source' | 'Time window', value: string) {
  await showFilters(page);
  await page.locator('.sit-list').getByRole('combobox', { name, exact: true }).click();
  await page.locator(`.sit-list [role="option"][data-value="${value}"]`).click();
}

async function showResults(page: Page) {
  const action = page.locator('.sit-view-timeline');
  if (await action.isVisible()) await action.click();
  await expect(page.locator('.sit-detail.is-open')).toBeVisible();
}

for (const [locale, retryLabel] of [['en', 'Retry'], ['zh', '重试'], ['fr', 'Réessayer']]) {
  const prefix = locale === 'en' ? '' : `/${locale}`;

  test(`${locale} situation markers retry without losing the shared place and filters`, async ({ page }) => {
    await mockHistory(page);
    let unavailable = true;
    let requests = 0;
    await page.route('**/situation/map-data.json', route => {
      requests += 1;
      return unavailable ? route.fulfill({ status: 503 }) : route.fallback();
    });
    await page.goto(`${prefix}/situation/?disease=D049&place=history-latest&window=all`);
    const retry = page.locator('.sit-note').getByRole('button', { name: retryLabel, exact: true });
    await expect(retry).toBeVisible();
    unavailable = false;
    await retry.click();
    await expect(page.locator('.sit-current-place .sit-alert')).toHaveCount(1);
    await expect(page.locator('.sit-filter-chip[data-filter="disease"]')).toBeVisible();
    expect(new URL(page.url()).searchParams.get('place')).toBe('history-latest');
    expect(new URL(page.url()).searchParams.get('window')).toBe('all');
    expect(requests).toBe(2);
  });

  test(`${locale} selected place retries its failed timeline without reloading`, async ({ page }) => {
    await mockHistory(page);
    let unavailable = true;
    let requests = 0;
    await page.route('**/situation/outbreaks/OB-history-a.json', route => {
      requests += 1;
      return unavailable ? route.fulfill({ status: 503 }) : route.fallback();
    });
    await page.goto(`${prefix}/situation/?disease=D049&place=history-latest&window=all`);
    await showResults(page);
    const retry = page.locator('.sit-current-place').getByRole('button', { name: retryLabel, exact: true });
    await expect(retry).toBeVisible();
    unavailable = false;
    await retry.click();
    await expect(page.locator('.sit-current-place .sit-source-link')).toHaveCount(2);
    await expect(retry).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get('place')).toBe('history-latest');
    expect(new URL(page.url()).searchParams.get('disease')).toBe('D049');
    expect(requests).toBe(2);
  });

  test(`${locale} all events can recover a partially failed overview without changing its filters`, async ({ page }) => {
    await mockHistory(page);
    let unavailable = true;
    let requests = 0;
    await page.route('**/situation/outbreaks/OB-history-b.json', route => {
      requests += 1;
      return unavailable ? route.fulfill({ status: 503 }) : route.fallback();
    });
    await page.goto(`${prefix}/situation/?disease=D049&window=all&timeline=all`);
    await showResults(page);
    const overview = page.locator('.sit-overview');
    await expect(overview).toHaveAttribute('aria-busy', 'false');
    await expect(overview.locator('.sit-alert')).toHaveCount(1);
    unavailable = false;
    await overview.getByRole('button', { name: retryLabel, exact: true }).click();
    await expect(overview.locator('.sit-alert')).toHaveCount(2);
    await expect(overview.locator('.sit-overview-error')).toHaveCount(0);
    expect(new URL(page.url()).searchParams.get('disease')).toBe('D049');
    expect(new URL(page.url()).searchParams.get('timeline')).toBe('all');
    expect(requests).toBe(2);
  });
}

test('a historical source remains selectable when the latest marker came from another channel', async ({ page }) => {
  await mockHistory(page);
  await page.goto('/situation/?disease=D049');
  await expect(page.locator('.sit-item')).toHaveCount(2);
  await selectFilter(page, 'Source', 'older-feed');
  await expect(page.locator('.sit-item')).toHaveCount(1);
  await expect(page.locator('.sit-item')).toContainText('Saudi Arabia');
  await page.locator('.sit-item').click();
  await expect(page.locator('.sit-current-place .sit-alert')).toHaveCount(1);
  await expect(page.locator('.sit-current-place .sit-source-link')).toHaveAttribute('href', 'https://example.test/history-older');
  await expect(page).toHaveURL(/source=older-feed/);
  await page.reload();
  await expect(page.locator('.sit-item')).toHaveCount(1);
  await expect(page.locator('.sit-current-place .sit-source-link')).toHaveAttribute('href', 'https://example.test/history-older');
});

test('structured monitoring coverage matches a generic report title and chips remove individual filters', async ({ page }) => {
  await mockHistory(page);
  await page.goto('/situation/?disease=D049&country=SA');
  await showFilters(page);
  await expect(page.locator('.sit-monitor-card')).toHaveCount(1);
  await expect(page.locator('.sit-monitor-card')).toContainText('Weekly public health roundup');
  await expect(page.locator('.sit-filter-chip[data-filter="disease"]')).toBeVisible();
  await expect(page.locator('.sit-filter-chip[data-filter="country"]')).toBeVisible();
  await page.locator('.sit-filter-chip[data-filter="disease"]').click();
  await expect(page.locator('.sit-filter-chip[data-filter="disease"]')).toHaveCount(0);
  await expect(page.locator('.sit-filter-chip[data-filter="country"]')).toBeVisible();
  expect(new URL(page.url()).searchParams.get('country')).toBe('SA');
  expect(new URL(page.url()).searchParams.has('disease')).toBe(false);
  await page.locator('.sit-filter-chip[data-filter="country"]').click();
  await expect(page.locator('.sit-filter-chip')).toHaveCount(0);
  await expect(page.locator('.sit-monitor-card')).toHaveCount(2);
  await selectFilter(page, 'Time window', '7');
  await expect(page.locator('.sit-filter-chip[data-filter="window"]')).toBeVisible();
  await page.locator('.sit-reset').click();
  await expect(page.locator('.sit-filter-chip')).toHaveCount(0);
});

test('all matching events spans outbreaks through legacy outbreak shards and switches back to one place', async ({ page }) => {
  const requests = await mockHistory(page);
  await page.goto('/situation/?disease=D049&place=history-latest');
  await expect(page.locator('.sit-current-place .sit-alert')).toHaveCount(1);
  await showResults(page);
  await page.locator('.sit-scope [data-scope="all"]').click();
  await expect(page.locator('.sit-scope [data-scope="all"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.sit-overview .sit-alert')).toHaveCount(2);
  await expect(page.locator('.sit-overview-count')).toContainText('2 data updates');
  await expect(page.locator('.sit-overview-count')).toContainText('3 reports');
  await expect(page.locator('.sit-overview .sit-alert-disease')).toHaveText(['MERS', 'MERS']);
  expect(requests.entityRequests).toEqual([]);
  expect([...new Set(requests.outbreakRequests)].sort()).toEqual(['/situation/outbreaks/OB-history-a.json', '/situation/outbreaks/OB-history-b.json']);
  await expect(page).toHaveURL(/timeline=all/);
  await page.reload();
  await expect(page.locator('.sit-overview .sit-alert')).toHaveCount(2);
  await showResults(page);
  await page.locator('.sit-overview .sit-alert[data-country="KE"] summary').click();
  await page.locator('.sit-overview .sit-alert[data-country="KE"] .sit-alert-focus').click();
  await expect(page.locator('.sit-overview')).toBeHidden();
  await expect(page.locator('.sit-scope [data-scope="place"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.sit-current-place .sit-alert')).toHaveCount(1);
  await expect(page.locator('.sit-current-place .sit-alert')).toHaveAttribute('data-country', 'KE');
  expect(new URL(page.url()).searchParams.get('place')).toBe('history-second');
  expect(new URL(page.url()).searchParams.has('timeline')).toBe(false);
  expect(requests.entityRequests).toEqual([]);
});

test('country overview deep links use legacy outbreak shards without a selected place', async ({ page }) => {
  const requests = await mockHistory(page);
  await page.goto('/situation/?country=SA&timeline=all');
  await expect(page.locator('.sit-overview .sit-alert')).toHaveCount(1);
  await expect(page.locator('.sit-overview-count')).toContainText('2 reports');
  await expect(page.locator('.sit-overview .sit-alert')).toHaveAttribute('data-country', 'SA');
  await expect(page.locator('.sit-scope [data-scope="all"]')).toHaveAttribute('aria-pressed', 'true');
  expect(new URL(page.url()).searchParams.get('timeline')).toBe('all');
  expect(requests.entityRequests).toEqual([]);
  expect([...new Set(requests.outbreakRequests)]).toEqual(['/situation/outbreaks/OB-history-a.json']);
});

test('mobile filter and results panels retain their scroll position while switching', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 0) >= 900, 'This behavior applies to the mobile panel switch.');
  await mockHistory(page, true);
  await page.goto('/situation/?disease=D049&place=history-latest');
  await expect(page.locator('.sit-current-place .sit-alert')).toHaveCount(1);
  await showFilters(page);
  await expect(page.locator('.sit-view-timeline')).toContainText('2');
  const list = page.locator('.sit-list');
  const detail = page.locator('.sit-detail');
  await list.evaluate(node => { node.scrollTop = 180; node.dispatchEvent(new Event('scroll')); });
  const leftPosition = await list.evaluate(node => node.scrollTop);
  expect(leftPosition).toBeGreaterThan(50);
  // The fixed header toggle remains reachable after scrolling either panel.
  await page.locator('.sit-bar-links button').click();
  await expect(detail).toBeVisible();
  await detail.evaluate(node => { node.scrollTop = 220; node.dispatchEvent(new Event('scroll')); });
  const rightPosition = await detail.evaluate(node => node.scrollTop);
  expect(rightPosition).toBeGreaterThan(50);
  await page.locator('.sit-bar-links button').click();
  await expect.poll(() => list.evaluate(node => node.scrollTop)).toBeCloseTo(leftPosition, 0);
  await page.locator('.sit-bar-links button').click();
  await expect.poll(() => detail.evaluate(node => node.scrollTop)).toBeCloseTo(rightPosition, 0);
  await showFilters(page);
  await page.locator('.sit-item').filter({ hasText: 'Kenya' }).click();
  await expect(detail).toBeVisible();
  await expect.poll(() => detail.evaluate(node => node.scrollTop)).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
