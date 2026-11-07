import { expect, test } from '@playwright/test';

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
  await page.getByLabel('更新类型').selectOption('knowledge');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.getByText('发布范围：D038')).toBeVisible();
  await page.getByLabel('更新类型').selectOption('all');
  await page.getByLabel('月份').selectOption('2026-10');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  await expect(page.locator('ol').getByText('论文数据')).toBeVisible();
  await page.getByLabel('月份').selectOption('2026-11');
  await page.getByLabel('更新类型').selectOption('website');
  await expect(page.getByText('数据已发布')).toHaveCount(0);
  await expect(page.getByRole('link', { name: '查看版本与版式更新' }).first()).toHaveAttribute('href', /\/zh\/changelog\/#v/);
});

test('unavailable feed does not show invented data', async ({ page }) => {
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ status: 503 }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  await expect(page.getByRole('link', { name: '查看版本与版式更新' }).first()).toBeVisible();
  await expect(page.getByText('数据已发布')).toHaveCount(0);
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
    await page.getByLabel('月份').selectOption('2026-10');
    await expect(page.getByText('数据已发布')).toHaveCount(0);
    await expect(page.getByRole('status')).toContainText('正在加载');
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
  await page.getByLabel('月份').selectOption('2026-10');
  await expect(page.locator('time[datetime="2026-10-07T12:00:00Z"]')).toBeVisible();
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  indexAvailable = true;
  await page.clock.fastForward(61_000);
  await expect(page.locator('.updates-status')).toBeEmpty();
  await expect(page.getByLabel('月份')).toHaveValue('2026-10');
});

test('monthly failures retain known events and malformed records cannot crash the page', async ({ page }) => {
  await page.clock.install();
  let available = true;
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: ['2026-11'] } }));
  await page.route('**/site-data/updates/2026-11.json', route => available
    ? route.fulfill({ json: { schema_version: 1, month: '2026-11', events: [
      { channel: 'literature', published_at: '2026-11-07T12:00:00Z' },
      { channel: 'literature', published_at: '2026-10-07T12:00:00Z' },
      { channel: 'unknown', published_at: '2026-11-07T12:00:00Z' },
      { channel: 'knowledge', published_at: '2026-11-07T12:00:00Z', scopes: [{}] },
    ] } }) : route.fulfill({ status: 503 }));
  await page.goto('/zh/data-updates/');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  available = false;
  await page.clock.fastForward(61_000);
  await expect(page.getByRole('status')).toContainText('数据更新记录暂不可用');
  await expect(page.getByText('数据已发布')).toHaveCount(1);
  available = true;
  await page.clock.fastForward(61_000);
  await expect(page.locator('.updates-status')).toBeEmpty();
});
