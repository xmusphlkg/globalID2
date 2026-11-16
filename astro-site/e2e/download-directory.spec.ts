import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';

const locales = [{ prefix: '', locale: 'en', name: 'Japan' }, { prefix: '/zh', locale: 'zh', name: '日本' }, { prefix: '/fr', locale: 'fr', name: 'Japon' }];
function publication(version = 1) {
  const part = (id: string) => ({ id: `202${version}`, label: `Period 202${version}`, is_current: true, record_count: version * 10,
    files: Object.fromEntries(['csv', 'json', 'xlsx'].map(format => [format, { url: `https://files.example.test/v${version}/${id}.${format}`, bytes: version * 1000 }])) });
  const base = { generated_at: `2026-10-0${version}`, record_count: version * 100, parts_count: 12 };
  const index = { schema_version: 1, generated_at: `2026-10-0${version}`, surveillance_release_id: 'download-source-release',
    countries: [{ ...base, kind: 'country', code: 'JP', id: 'jp', name_en: 'Japan', name_zh: '日本', parts: [part('jp')] },
      { ...base, kind: 'country', code: 'JP-13', id: 'jp-13', name_en: 'Tokyo', name_zh: '东京都', parts: [part('tokyo')] }],
    diseases: [{ ...base, kind: 'disease', disease_id: 'D038', id: 'd038', slug: 'influenza', name_en: 'Influenza', name_zh: '流感', country_count: version + 1, parts: [part('influenza')] }] };
  const meta = { countries: [{ code: 'JP', location_type: 'country' }, { code: 'JP-13', location_type: 'subdivision', parent_code: 'JP' }] };
  const objects = new Map<string, string>();
  const files = Object.fromEntries(Object.entries({ 'downloads/index.json': index, 'meta.json': meta }).map(([key, value]) => {
    const body = JSON.stringify(value), sha256 = createHash('sha256').update(body).digest('hex'), url = `/site-data/objects/${sha256}.json`;
    objects.set(url, body);
    return [key, { url, sha256, bytes: Buffer.byteLength(body) }];
  }));
  return { manifest: { schema_version: 1, release_id: `directory-${version}`, files }, objects };
}

for (const { prefix, locale, name } of locales) {
  test(`${locale} directory refreshes every count, period and direct link from one pinned compact publication`, async ({ page, request }) => {
    const first = publication(1), next = publication(2), objects = new Map([...first.objects, ...next.objects]);
    let current = first, manifestReads = 0, legacyReads = 0;
    const datasetReads: string[] = [], errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/current.json', route => { manifestReads++; return route.fulfill({ json: current.manifest }); });
    await page.route('**/site-data/objects/*.json', route => {
      const path = new URL(route.request().url()).pathname; datasetReads.push(path);
      return route.fulfill({ body: objects.get(path), contentType: 'application/json' });
    });
    await page.route(/\/site-data\/(?:downloads\/index|downloads|(?:countries|diseases)\/.*-summary)\.json$/, route => { legacyReads++; return route.fulfill({ status: 503 }); });
    const path = `${prefix}/downloads/`;
    const hashHtml = async () => createHash('sha256').update(await (await request.get(path)).body()).digest('hex');
    const before = await hashHtml();
    await page.goto(path);
    const root = page.locator('.downloads-page');
    await expect(root).toHaveAttribute('data-surveillance-version', 'directory-1');
    await expect(page.locator('[data-download-row]')).toHaveCount(2);
    await expect(page.locator('[data-download-metric="countries"]')).toHaveText('1');
    await expect(page.locator('[data-download-metric="diseases"]')).toHaveText('1');
    await expect(page.locator('[data-download-metric="records"]')).toHaveText('200');
    await expect(page.locator('[data-download-metric="date"]')).toContainText('2026');
    await expect(page.locator('[data-download-subdivision-note]')).toContainText('1');
    const country = page.locator('[data-download-row][data-kind="country"]');
    await expect(country.locator('[data-download-name]')).toHaveText(name);
    await expect(country.locator('[data-download-period]')).toHaveText('Period 2021');
    await expect(country.locator('[data-download-detail]')).toContainText('100');
    if (locale === 'zh') await expect(country.locator('[data-download-detail]')).toContainText('12 个时间范围');
    await expect(country.locator('[data-download-format="csv"]')).toHaveAttribute('href', 'https://files.example.test/v1/jp.csv');
    current = next;
    await page.locator('input[name="dataset"]').fill('grippe');
    await expect(page.locator('[data-download-row]:visible')).toHaveCount(1);
    await page.locator('input[name="dataset"]').fill('');
    await page.locator('select[name="sort"]').selectOption('records');
    await page.locator('[data-download-kind="country"]').click();
    await expect(country).toBeVisible();
    await expect(country.locator('[data-download-format="csv"]')).toHaveAttribute('href', 'https://files.example.test/v1/jp.csv');
    expect(manifestReads).toBe(1);
    expect(datasetReads).toHaveLength(2);
    expect(legacyReads).toBe(0);
    await page.reload();
    await expect(root).toHaveAttribute('data-surveillance-version', 'directory-2');
    await expect(page.locator('[data-download-metric="records"]')).toHaveText('400');
    await expect(country.locator('[data-download-period]')).toHaveText('Period 2022');
    await expect(country.locator('[data-download-format="csv"]')).toHaveAttribute('href', 'https://files.example.test/v2/jp.csv');
    await expect(country.locator('[data-download-format="csv"] small')).toHaveText('2 KB');
    expect(await hashHtml()).toBe(before);
    expect(legacyReads).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });

  test(`${locale} directory hides stale metadata and links after integrity failure and retries the pinned object`, async ({ page }) => {
    const current = publication(); let corrupt = true;
    await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/current.json', route => route.fulfill({ json: current.manifest }));
    await page.route('**/site-data/objects/*.json', route => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({ body: corrupt && path === current.manifest.files['downloads/index.json'].url ? '{}' : current.objects.get(path), contentType: 'application/json' });
    });
    await page.goto(`${prefix}/downloads/`);
    await expect(page.locator('.downloads-page')).toHaveAttribute('data-download-state', 'error');
    await expect(page.locator('.formats a:visible')).toHaveCount(0);
    await expect(page.locator('[data-download-metric="date"]')).not.toBeVisible();
    await expect(page.locator('[data-download-status]')).toBeVisible();
    corrupt = false;
    await page.locator('[data-download-retry]').click();
    await expect(page.locator('.downloads-page')).toHaveAttribute('data-download-state', 'publication');
    await expect(page.locator('.formats a:visible')).toHaveCount(6);
  });

  test(`${locale} directory retains the supported legacy UI when neither manifest exists`, async ({ page }) => {
    await page.route('**/site-data/bootstrap.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/current.json', route => route.fulfill({ status: 404 }));
    await page.goto(`${prefix}/downloads/`);
    await expect(page.locator('.downloads-page')).toHaveAttribute('data-download-state', 'legacy');
    await expect(page.locator('input[name="dataset"]')).toBeVisible();
    await expect(page.locator('[data-download-retry]')).not.toBeVisible();
  });
}
