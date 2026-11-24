import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';

const part = (country_code: string, disease_id: string, id = '2026-2029') => ({
  country_code, disease_id, id, label: `${disease_id} · ${id}`, record_count: 1,
  date_range: { start: '2026-01-01', end: '2026-11-01' },
  files: Object.fromEntries(['csv', 'json', 'xlsx'].map(format => [format, {
    url: `https://files.example.test/cells/${country_code.toLowerCase()}/${disease_id.toLowerCase()}/${id}.${format}`,
    filename: `globalid-${country_code.toLowerCase()}-${disease_id.toLowerCase()}-${id}.${format}`, bytes: 100,
  }])),
});
const baseParts = [part('JP', 'D007'), part('JP', 'D038', '2020-2025'), part('JP', 'D038'), part('BG', 'D038')];
async function publication(page: Page, parts = baseParts) {
  const countries = ['JP', 'BG'].map(code => ({ kind: 'country', id: code.toLowerCase(), code,
    name_en: code === 'JP' ? 'Japan' : 'Bulgaria', name_zh: code === 'JP' ? '日本' : '保加利亚',
    generated_at: '2026-11-19', record_count: parts.filter(part => part.country_code === code).length,
    parts: parts.filter(part => part.country_code === code) }));
  const diseases = ['D007', 'D038'].map(disease_id => ({ kind: 'disease', id: disease_id.toLowerCase(), disease_id,
    name_en: disease_id === 'D038' ? 'Influenza' : 'Cholera', name_zh: disease_id === 'D038' ? '流感' : '霍乱',
    slug: disease_id === 'D038' ? 'influenza' : 'cholera', generated_at: '2026-11-19',
    record_count: parts.filter(part => part.disease_id === disease_id).length,
    parts: parts.filter(part => part.disease_id === disease_id) }));
  const compact = (entry: any) => ({ ...entry, parts_count: entry.parts.length, parts: entry.parts.slice(0, 1) });
  const logical: Record<string, unknown> = {
    'meta.json': { countries: countries.map(({ code, name_en, name_zh }) => ({ code, name_en, name_zh })) },
    'downloads/index.json': { schema_version: 1, generated_at: '2026-11-19', countries: countries.map(compact), diseases: diseases.map(compact) },
  };
  for (const entry of countries) logical[`countries/${entry.id}-summary.json`] = { schema_version: 1, country_code: entry.code, download: entry };
  for (const entry of diseases) logical[`diseases/${entry.id}-summary.json`] = { schema_version: 1, disease_id: entry.disease_id, download: entry };
  const files: Record<string, unknown> = {}, bodies = new Map<string, string>();
  for (const [key, value] of Object.entries(logical)) {
    const body = JSON.stringify(value), sha256 = createHash('sha256').update(body).digest('hex');
    const url = `objects/${sha256}.json`; files[key] = { url, sha256, bytes: Buffer.byteLength(body) }; bodies.set(url, body);
  }
  await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/current.json', route => route.fulfill({ json: { schema_version: 1, release_id: 'test-cells', files } }));
  await page.route('**/site-data/objects/*.json', route => {
    const key = new URL(route.request().url()).pathname.replace('/site-data/', '');
    return route.fulfill({ body: bodies.get(key) || '{}', contentType: 'application/json', status: bodies.has(key) ? 200 : 404 });
  });
  await page.route('https://files.example.test/**', route => route.fulfill({ body: 'date,cases\n2026-01-01,1\n', contentType: 'text/csv' }));
  return { files: files as Record<string, { url: string }>, bodies };
}

for (const [language, now] of [['zh', '至今'], ['en', 'now'], ['fr', 'aujourd’hui']]) {
  for (const year of [2026, 2030]) {
    test(`${language} time filters and file titles follow the calendar in ${year}`, async ({ page }) => {
      await page.clock.setFixedTime(new Date(`${year}-01-01T12:00:00+08:00`));
      await publication(page, [part('JP', 'D038'), ...(year === 2030 ? [part('JP', 'D038', '2030-2034')] : [])]);
      await page.goto(`${language === 'en' ? '' : `/${language}`}/downloads/?country=JP&format=csv`);
      const center = page.locator('[data-download-center]');
      const timeFilter = center.locator('[data-download-time-filter]');
      const bridgeLabel = year === 2026 ? `2026–${now}` : '2026–2029';
      await expect(timeFilter.locator('option[value="2026-2029"]')).toHaveText(bridgeLabel);
      await expect(center.locator('[data-download-time="JP/D038/2026-2029"] h3')).toContainText(bridgeLabel);
      if (year === 2030) {
        await expect(timeFilter.locator('option[value="2030-2034"]')).toHaveText(`2030–${now}`);
        await expect(center.locator('[data-download-time="JP/D038/2030-2034"] h3')).toContainText(`2030–${now}`);
      }
      await timeFilter.selectOption('2026-2029');
      await expect(center.locator('[data-download-file]')).toHaveCount(1);
      await expect(page).toHaveURL(/window=2026-2029/);
      await expect(center.locator('[data-download-file] a')).toHaveAttribute('href', 'https://files.example.test/cells/jp/d038/2026-2029.csv');
    });
  }
}

test('country deep link shows multiple diseases and downloads the selected cell', async ({ page }) => {
  await publication(page);
  await page.goto('/zh/downloads/?country=JP');
  const center = page.locator('[data-download-center]');
  await expect(center.locator('[data-download-file]')).toHaveCount(9);
  await expect(center.locator('[data-download-country-filter]')).toHaveValue('JP');
  await center.locator('[data-download-disease-filter]').selectOption('D038');
  await center.locator('[data-download-time-filter]').selectOption('2026-2029');
  await center.locator('[data-download-format-filter]').selectOption('csv');
  await expect(center.locator('[data-download-file]')).toHaveCount(1);
  await expect(center.locator('[data-download-file]')).toContainText('日本');
  await expect(center.locator('[data-download-file]')).toContainText('流感');
  await expect(page).toHaveURL(/country=JP.*disease=D038.*window=2026-2029.*format=csv/);
  const saved = page.waitForEvent('download');
  await center.getByRole('button', { name: '下载 CSV', exact: true }).click();
  expect((await saved).suggestedFilename()).toBe('globalid-jp-d038-2026-2029.csv');
});

test('disease deep link offers multiple countries and resolves the same shared file', async ({ page }) => {
  await publication(page);
  await page.goto('/fr/downloads/?disease=D038&window=2026-2029&format=csv');
  const center = page.locator('[data-download-center]');
  await expect(center.locator('[data-download-file]')).toHaveCount(2);
  await center.locator('[data-download-country-filter]').selectOption('JP');
  await center.locator('[data-download-time-filter]').selectOption('2026-2029');
  await expect(center.locator('[data-download-file]')).toHaveCount(1);
  await expect(center.locator('[data-download-file] a')).toHaveAttribute('href', 'https://files.example.test/cells/jp/d038/2026-2029.csv');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('large selections paginate and the unscoped center does not load every country', async ({ page }) => {
  await publication(page, Array.from({ length: 25 }, (_, i) => part('JP', 'D038', `${1900 + i}`)));
  await page.goto('/zh/downloads/');
  const center = page.locator('[data-download-center]');
  await expect(center.locator('[data-download-country-filter]')).toBeEnabled();
  await expect(center.locator('[data-download-file]')).toHaveCount(0);
  await expect(center).toContainText('选择国家／地区或疾病');
  await center.locator('[data-download-country-filter]').selectOption('JP');
  await expect(center.locator('[data-download-file]')).toHaveCount(24);
  const first = await center.locator('[data-download-file] a').first().getAttribute('href');
  await center.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(center.locator('[data-download-file]')).toHaveCount(24);
  expect(await center.locator('[data-download-file] a').first().getAttribute('href')).not.toBe(first);
});


test('an unavailable index can retry without navigating or using static links', async ({ page }) => {
  const state = await publication(page);
  const url = state.files['downloads/index.json'].url;
  let available = false;
  await page.route(`**/site-data/${url}`, route => available
    ? route.fulfill({ body: state.bodies.get(url), contentType: 'application/json' })
    : route.fulfill({ status: 503 }));
  await page.goto('/zh/downloads/');
  const center = page.locator('[data-download-center]');
  await expect(center).toContainText('下载目录暂不可用');
  await expect(center.locator('[data-download-file]')).toHaveCount(0);
  available = true;
  await center.getByRole('button', { name: '重试', exact: true }).click();
  await expect(center.locator('[data-download-country-filter]')).toBeEnabled();
  await expect(center.locator('[data-download-country-filter] option[value="JP"]')).toHaveCount(1);
});

test('corrupt summary data cannot expose stale download URLs', async ({ page }) => {
  const state = await publication(page);
  const url = state.files['countries/jp-summary.json'].url;
  let legacyReads = 0;
  await page.route(`**/site-data/${url}`, route => route.fulfill({ json: { schema_version: 1, download: {} } }));
  await page.route('**/site-data/downloads/countries/jp.json', route => { legacyReads++; return route.fulfill({ status: 500 }); });
  await page.goto('/zh/downloads/?country=JP');
  const center = page.locator('[data-download-center]');
  await expect(center).toContainText('文件列表暂不可用');
  await expect(center.locator('[data-download-file]')).toHaveCount(0);
  expect(legacyReads).toBe(0);
  await expect(center.locator('a[href*="/main/countries/"], a[href*="/main/diseases/"]')).toHaveCount(0);
});

test('a missing publication shows an error without reading legacy archives', async ({ page }) => {
  let legacyReads = 0;
  await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/downloads/**', route => { legacyReads++; return route.fulfill({ status: 500 }); });
  await page.goto('/zh/downloads/?country=JP');
  const center = page.locator('[data-download-center]');
  await expect(center).toContainText('下载目录暂不可用');
  await expect(center.locator('[data-download-file]')).toHaveCount(0);
  expect(legacyReads).toBe(0);
});
