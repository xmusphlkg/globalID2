import { expect, test } from '@playwright/test';
import { changelogReleases } from '../src/data/changelog';

test.beforeEach(async ({ page }) => {
  await page.route('**/site-data/updates/changelog.json', route => route.fulfill({ json: { schema_version: 1, releases: changelogReleases } }));
});

test('footer links to the localized updates page and header stays clear', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [] } }));
  for (const [path, label, fromLabel, chartLabel] of [
    ['/data-updates/', 'Data & site updates', 'From (UTC)', /Daily publication records/],
    ['/zh/data-updates/', '网站与数据更新', '起始日期（UTC）', /每日发布记录/],
    ['/fr/data-updates/', 'Mises à jour du site et des données', 'Depuis (UTC)', /Publications quotidiennes/],
  ] as const) {
    await page.goto(path);
    await expect(page.locator('footer').getByRole('link', { name: label, exact: true })).toHaveAttribute('href', path);
    await expect(page.locator('header.site-header').getByRole('link', { name: label, exact: true })).toHaveCount(0);
    await expect(page.getByLabel(fromLabel)).toHaveValue('2026-11-01');
    await expect(page.getByRole('img', { name: chartLabel })).toBeVisible();
    await expect.poll(() => page.locator('.updates-trend-chart svg').evaluate(svg =>
      Math.abs(Number(svg.getAttribute('viewBox')?.split(' ')[2]) - svg.getBoundingClientRect().width) < 2,
    )).toBe(true);
    for (const theme of ['light', 'dark']) {
      await page.locator('html').evaluate((element, value) => element.setAttribute('data-theme', value), theme);
      await expect(page.locator('.updates-intro')).toHaveClass(/academic-hero/);
      await expect(page.locator('.updates-panel')).toHaveClass(/card/);
      await expect(page.locator('.updates-controls .site-control-input').first()).toHaveClass(/site-control-input/);
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
  }
});

test('shows real published events, filters channels, and loads monthly archives', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11', '2026-10'], counts: { surveillance: 54, situation: 24, literature: 13, knowledge: 0 } } }));
  await page.route('**/site-data/updates/2026-11.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [
    { channel: 'surveillance', published_at: '2026-11-07T12:00:00Z', scopes: ['JP'] },
    { channel: 'situation', published_at: '2026-11-07T11:00:00Z' },
    { channel: 'literature', published_at: '2026-11-07T10:00:00Z' },
    { channel: 'knowledge', published_at: '2026-11-07T09:00:00Z', scopes: ['D038'] },
  ] } }));
  await page.route('**/site-data/updates/2026-10.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-10', events: [{ channel: 'literature', published_at: '2026-10-04T10:00:00Z' }] } }));
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => route.fulfill({ json: { total_countries: 294, total_diseases: 239, countries: [{ data_available: true, record_count: 180 }, { data_available: true, record_count: 220 }] } }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByRole('heading', { name: '网站与数据更新', level: 1, exact: true })).toBeVisible();
  await expect(page.getByText('294', { exact: true })).toBeVisible();
  await expect(page.getByText('400', { exact: true })).toBeVisible();
  await expect(page.getByText('91', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '查看版本与版式更新' }).first()).toBeVisible();
  await expect(page.getByText('数据已发布')).toHaveCount(4);
  await expect(page.locator('.updates-trend-counts > div').filter({ hasText: '数据更新' }).locator('dd')).toHaveText('4');
  await expect(page.locator('.updates-trend-note, .updates-metrics-note')).toHaveCount(0);
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
  await page.getByText('查看每日数量').click();
  await expect(page.getByRole('row', { name: /2026-11-07/ }).getByRole('cell')).toHaveText('6');
  await page.getByLabel('更新类型').selectOption('knowledge');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.getByText('发布范围：D038')).toBeVisible();
  await expect(page.locator('.updates-trend-counts > div').filter({ hasText: '数据更新' }).locator('dd')).toHaveText('1');
  await expect(page.getByRole('row', { name: /2026-11-07/ }).getByRole('cell')).toHaveText('1');
  await page.getByLabel('更新类型').selectOption('all');
  await page.getByLabel('截止日期（UTC）').fill('2026-10-31');
  await page.getByLabel('起始日期（UTC）').fill('2026-10-01');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.locator('ol').getByText('论文数据')).toBeVisible();
  await page.getByLabel('起始日期（UTC）').fill('2026-11-01');
  await page.getByLabel('截止日期（UTC）').fill('2026-11-30');
  await page.getByLabel('更新类型').selectOption('website');
  await expect(page.getByText('数据已发布')).toHaveCount(0);
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
  await expect(page.getByRole('link', { name: '查看版本与版式更新' }).first()).toHaveAttribute('href', /\/zh\/changelog\/#v/);
});

test('unavailable feed does not show invented data', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ status: 503 }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  await expect(page.getByRole('link', { name: '查看版本与版式更新' }).first()).toBeVisible();
  await expect(page.getByText('数据已发布')).toHaveCount(0);
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toHaveCount(0);
  await expect(page.getByText('查看每日数量')).toHaveCount(0);
  await page.getByLabel('更新类型').selectOption('website');
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
});

test('changing month hides old events until the selected archive arrives', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11', '2026-10'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [{ channel: 'surveillance', published_at: '2026-11-07T12:00:00Z', scopes: ['JP'] }] } }));
  let releaseArchive!: () => void;
  const archiveReady = new Promise<void>(resolve => { releaseArchive = resolve; });
  await page.route('**/site-data/updates/2026-10.json', async route => {
    await archiveReady;
    await route.fulfill({ json: { schema_version: 1, month: '2026-10', events: [{ channel: 'literature', published_at: '2026-10-07T12:00:00Z' }] } });
  });
  await page.goto('/zh/data-updates/');
  await expect(page.getByText('发布范围：JP')).toBeVisible();
  try {
    await page.getByLabel('截止日期（UTC）').fill('2026-10-31');
    await page.getByLabel('起始日期（UTC）').fill('2026-10-01');
    await expect(page.getByText('数据已发布')).toHaveCount(0);
    await expect(page.getByRole('status').last()).toContainText('正在读取');
  } finally { releaseArchive(); }
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.locator('ol').getByText('论文数据')).toBeVisible();
  await expect(page.getByText('发布范围：JP')).toHaveCount(0);
});

test('index errors survive a successful archive read and recover on the next poll', async ({ page }) => {
  await page.clock.install();
  let indexAvailable = true;
  await page.route('**/site-data/updates/latest.json', route => indexAvailable
    ? route.fulfill({ json: { schema_version: 1, months: ['2026-11', '2026-10'] } })
    : route.fulfill({ status: 503 }));
  for (const month of ['2026-11', '2026-10']) {
    await page.route(`**/site-data/updates/${month}.json`, route => route.fulfill({ json: { schema_version: 1, month, events: [{ channel: 'literature', published_at: `${month}-07T12:00:00Z` }] } }));
  }
  await page.goto('/zh/data-updates/');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  indexAvailable = false;
  await page.clock.fastForward(61_000);
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  await page.getByLabel('截止日期（UTC）').fill('2026-10-31');
  await page.getByLabel('起始日期（UTC）').fill('2026-10-01');
  await expect(page.locator('time[datetime="2026-10-07T12:00:00Z"]')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  indexAvailable = true;
  await page.clock.fastForward(61_000);
  await expect(page.locator('.updates-status')).toBeEmpty();
  await expect(page.getByLabel('起始日期（UTC）')).toHaveValue('2026-10-01');
  await expect(page.getByLabel('截止日期（UTC）')).toHaveValue('2026-10-31');
});

test('monthly failures retain known events and malformed records cannot crash the page', async ({ page }) => {
  await page.clock.install();
  let available = true;
  let malformed = true;
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => available
    ? route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [
      { channel: 'literature', published_at: '2026-11-07T12:00:00Z' },
      ...(malformed ? [{ channel: 'literature', published_at: '2026-10-07T12:00:00Z' },
      { channel: 'unknown', published_at: '2026-11-07T12:00:00Z' },
      { channel: 'knowledge', published_at: '2026-11-07T12:00:00Z', scopes: [{}] }] : []),
    ] } }) : route.fulfill({ status: 503 }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.getByRole('status')).toContainText('不完整');
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toHaveCount(0);
  available = false;
  await page.clock.fastForward(61_000);
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  available = true;
  malformed = false;
  await page.clock.fastForward(61_000);
  await expect(page.locator('.updates-status')).toBeEmpty();
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
});

test('UTC range filtering and daily counts include offset timestamps on the correct day', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [
    { channel: 'literature', published_at: '2026-12-01T00:30:00+08:00' },
    { channel: 'literature', published_at: '2026-10-31T23:30:00-03:00' },
  ] } }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByText('数据已发布')).toHaveCount(2);
  await page.getByLabel('更新类型').selectOption('literature');
  await page.getByLabel('起始日期（UTC）').fill('2026-11-30');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.locator('time[datetime="2026-12-01T00:30:00+08:00"]')).toBeVisible();
  await page.getByText('查看每日数量').click();
  await expect(page.getByRole('row', { name: /2026-11-30/ }).getByRole('cell')).toHaveText('1');
  await page.getByLabel('起始日期（UTC）').fill('2026-11-01');
  await page.getByLabel('截止日期（UTC）').fill('2026-11-01');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.locator('time[datetime="2026-10-31T23:30:00-03:00"]')).toBeVisible();
  await expect(page.getByRole('row', { name: /2026-11-01/ }).getByRole('cell')).toHaveText('1');
});

test('incomplete, reversed and oversized date ranges explain how to recover', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [] } }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
  await page.getByLabel('截止日期（UTC）').fill('');
  await expect(page.getByRole('status')).toHaveText('请填写有效的起始日期和截止日期。');
  await expect(page.getByLabel('截止日期（UTC）')).toHaveAttribute('aria-invalid', 'true');
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toHaveCount(0);
  await page.getByLabel('截止日期（UTC）').fill('2026-10-01');
  await expect(page.getByRole('status')).toHaveText('起始日期不能晚于截止日期。');
  await page.getByLabel('起始日期（UTC）').fill('2024-01-01');
  await expect(page.getByRole('status')).toHaveText('请选择不超过 24 个月的范围。');
  await page.getByLabel('起始日期（UTC）').fill('2026-10-01');
  await expect(page.getByLabel('截止日期（UTC）')).toHaveAttribute('aria-invalid', 'false');
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
  await page.getByText('查看每日数量').click();
  await expect(page.getByRole('row', { name: /2026-10-01/ }).getByRole('cell')).toHaveText('0');
});

test('a missing month never becomes a zero-count daily chart', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11', '2026-10'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [{ channel: 'literature', published_at: '2026-11-07T12:00:00Z' }] } }));
  await page.route('**/site-data/updates/2026-10.json', route => route.fulfill({ status: 503 }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toBeVisible();
  await page.getByLabel('起始日期（UTC）').fill('2026-10-01');
  await expect(page.getByRole('status')).toContainText('不完整');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.getByRole('img', { name: /每日发布记录/ })).toHaveCount(0);
  await expect(page.getByText('查看每日数量')).toHaveCount(0);
});
