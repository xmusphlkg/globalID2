// Prepared 2026-11-16. No request mocks, deployment commands, or production writes.
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
const require = createRequire(new URL('../../../../../astro-site/package.json', import.meta.url));
const { chromium, expect } = require('@playwright/test');
const required = name => { const value = process.env[name]; if (!value) throw new Error(`Required: ${name}`); return value; };
const origin = new URL(required('GIDS_SMOKE_ORIGIN')).origin;
const expected = {
  '/release.json': required('GIDS_EXPECTED_SITE_RELEASE'),
  '/site-data/current.json': required('GIDS_EXPECTED_DATA_RELEASE'),
  '/site-data/situation/current.json': required('GIDS_EXPECTED_SITUATION_RELEASE'),
};
if (process.env.GIDS_EXPECTED_RESEARCH_RELEASE) expected['/site-data/research-publication/current.json'] = process.env.GIDS_EXPECTED_RESEARCH_RELEASE;
const output = resolve(required('GIDS_SMOKE_OUTPUT'));
const result = { origin, expected, started_at: new Date().toISOString(), identities: [], objects: [], browsers: [], rss: [], status: 'running' };
await mkdir(output, { recursive: true });
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
async function read(url, init = {}) {
  const response = await fetch(new URL(url, origin), { ...init, headers: { 'Cache-Control': 'no-cache', ...init.headers }, signal: AbortSignal.timeout(60000) });
  expect(response.status, url).toBe(200);
  return response;
}
async function identities(stage) {
  const manifests = {};
  for (const [path, release] of Object.entries(expected)) {
    const response = await read(path), bytes = Buffer.from(await response.arrayBuffer());
    const payload = JSON.parse(bytes.toString());
    expect(payload.release_id, `${stage} ${path}`).toBe(release);
    result.identities.push({ stage, path, release_id: payload.release_id, sha256: digest(bytes), cache_control: response.headers.get('cache-control') });
    manifests[path] = payload;
  }
  return manifests;
}
async function immutable(path, manifest, keys) {
  for (const key of keys) {
    const entry = manifest.files?.[key];
    expect(entry, `${path}: ${key}`).toBeTruthy();
    expect(entry.url).toMatch(/^objects\/[a-f0-9]{64}\.json$/);
    const url = new URL(entry.url, new URL(path, origin));
    const bytes = Buffer.from(await (await read(url)).arrayBuffer());
    expect(digest(bytes), key).toBe(entry.sha256);
    expect(bytes.length, key).toBe(entry.bytes);
    result.objects.push({ key, url: url.href, bytes: bytes.length, sha256: digest(bytes) });
  }
}
const labels = {
  en: { disease: 'Disease', from: 'Start date', view: 'Group trend by', metric: 'Measure', download: 'Download data' },
  zh: { disease: '疾病', from: '开始日期', view: '趋势汇总周期', metric: '指标', download: '下载数据' },
  fr: { disease: 'Maladie', from: 'Date de début', view: 'Regrouper la tendance', metric: 'Indicateur', download: 'Télécharger les données' },
};
let browser;
try {
  const manifests = await identities('before');
  await immutable('/site-data/current.json', manifests['/site-data/current.json'], ['home-summary.json', 'subdivision-maps/index.json']);
  await immutable('/site-data/situation/current.json', manifests['/site-data/situation/current.json'], ['feed.json']);
  browser = await chromium.launch({ headless: true });
  for (const width of [390, 1280]) for (const lang of ['en', 'zh', 'fr']) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
    const page = await context.newPage(), errors = [], t = labels[lang], prefix = lang === 'en' ? '/' : `/${lang}/`;
    page.on('pageerror', error => errors.push({ url: page.url(), message: error.message }));
    for (const route of ['', 'diseases/dengue/', 'situation/', 'subscribe/']) {
      const response = await page.goto(`${origin}${prefix}${route}`, { timeout: 90000, waitUntil: 'networkidle' });
      expect(response?.status()).toBe(200);
      await expect(page.locator('h1').first()).toBeVisible();
      expect(await page.locator('html').getAttribute('lang')).toMatch(lang === 'zh' ? /^zh/ : new RegExp(`^${lang}`));
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), page.url()).toBeLessThanOrEqual(1);
      await page.screenshot({ path: `${output}/${lang}-${width}-${route.split('/')[0] || 'home'}.png` });
    }
    for (const filter of ['country', 'disease']) {
      await page.goto(`${origin}${prefix}subscribe/`, { timeout: 90000 });
      const rss = page.locator('.situation-rss');
      await rss.scrollIntoViewIfNeeded();
      await expect(rss.locator('a[type="application/rss+xml"]')).toHaveAttribute('href', `/site-data/situation/rss.xml?lang=${lang}`);
      const select = rss.locator(`select[name="${filter}"]`);
      const value = await select.locator('option').nth(1).getAttribute('value');
      expect(value).toBeTruthy();
      await select.selectOption(value);
      const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/site-data/situation/rss.xml');
      await select.locator('xpath=..').getByRole('button').click();
      const response = await responsePromise;
      await page.waitForURL('**/site-data/situation/rss.xml?*');
      const url = new URL(page.url()), xml = await response.text();
      expect(url.searchParams.get('lang')).toBe(lang);
      expect(url.searchParams.get(filter)).toBe(value);
      expect(response.status()).toBe(200);
      expect(response.headers()['content-type']).toContain('application/rss+xml');
      expect(response.headers()['x-gids-situation-release']).toBe(expected['/site-data/situation/current.json']);
      expect(xml).toContain(`<language>${lang === 'zh' ? 'zh-CN' : lang}</language>`);
      expect(await page.evaluate(() => document.querySelectorAll('parsererror').length)).toBe(0);
      result.rss.push({ width, lang, filter, value, url: url.href, release_id: response.headers()['x-gids-situation-release'], sha256: digest(xml) });
    }
    await page.goto(`${origin}${prefix}countries/us/?mapDisease=D094&mapFrom=2025-01-01&mapTo=2025-12-31&mapCompare=none&mapTab=table#regional-distribution`, { timeout: 90000 });
    const map = page.locator('[data-subdivision-country="US"]');
    await map.scrollIntoViewIfNeeded();
    await expect(map.getByRole('combobox', { name: t.disease, exact: true })).toHaveValue('D094', { timeout: 60000 });
    await expect(map.getByLabel(t.from, { exact: true })).toHaveValue('2025-01-01');
    await expect(map.locator('.subdivision-map-table tbody tr')).toHaveCount(56);
    const values = await map.locator('.subdivision-map-table tbody').innerText();
    await map.getByRole('combobox', { name: t.view, exact: true }).selectOption('annual');
    await expect.poll(() => map.locator('.subdivision-map-table tbody').innerText()).toBe(values);
    await map.getByRole('combobox', { name: t.metric, exact: true }).selectOption('cases');
    await expect.poll(() => map.locator('.subdivision-map-table tbody').innerText()).not.toBe(values);
    const downloadPromise = page.waitForEvent('download');
    await map.getByRole('button', { name: t.download, exact: true }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('US-D094-2025-01-01-2025-12-31.csv');
    expect(await readFile(await download.path(), 'utf8')).toContain('US-CA');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: `${output}/${lang}-${width}-map.png` });
    expect(errors).toEqual([]);
    result.browsers.push({ width, lang, page_errors: errors, map_regions: 56, csv_download: true, no_horizontal_overflow: true });
    await context.close();
  }
  await identities('after');
  result.status = 'passed';
} catch (error) {
  result.status = 'failed';
  result.error = error.stack || String(error);
  process.exitCode = 1;
} finally {
  await browser?.close();
  result.finished_at = new Date().toISOString();
  await writeFile(`${output}/postpublish-smoke.json`, `${JSON.stringify(result, null, 2)}\n`);
  console.log(JSON.stringify({ status: result.status, output, browsers: result.browsers.length, rss: result.rss.length, error: result.error }));
}
