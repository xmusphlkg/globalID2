import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';

const hash = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
function publication(version: number) {
  const day = version === 1 ? '2026-11-07' : '2026-11-08';
  const alert = { alert_id: 'situation-update', outbreak_id: 'OB-runtime', notice_id: 'notice-runtime', source_id: 'official', source_label_en: 'Official source', agency_en: null, title_en: 'Reviewed report', url: 'https://example.test/report', disease_id: 'D038', disease_slug: 'influenza', disease_label: 'Influenza', disease_name_en: 'Influenza', country_code: 'JP', country_name_en: 'Japan', admin_area: null, status: 'ongoing', cases: version * 17, deaths: 0, count_basis: 'confirmed', as_of: day, published_date: day, quote: `Situation release ${version}.`, summary_en: `Reviewed update ${version}.`, ai: null };
  const week = { schema_version: 'gids.situation_alerts.v1', period_key: '2026-W45', new_outbreaks: ['OB-runtime'], updated_outbreaks: [], new_places: version, alerts: version, outbreaks: [{ outbreak_id: 'OB-runtime', disease_id: 'D038', disease_name_en: 'Influenza', disease_label: 'Influenza', country_codes: ['JP'], first_published: day }] };
  const entries = {
    'map-data.json': { schema_version: 'gids.situation_alerts.v1', generated_at: day, as_of: day, points: [alert], monitoring: [], active_outbreaks: version, sources_ok: 1, source_count: 1 },
    'outbreaks/OB-runtime.json': { schema_version: 'gids.situation_alerts.v1', outbreak_id: 'OB-runtime', disease_id: 'D038', disease_label: 'Influenza', disease_name_en: 'Influenza', first_published: day, last_published: day, country_codes: ['JP'], alert_count: 1, notice_count: 1, source_ids: ['official'], status: 'active', as_of: day, alerts: [alert] },
    'by-country/JP.json': { schema_version: 'gids.situation_alerts.v1', alerts: [alert] },
    'by-disease/D038.json': { schema_version: 'gids.situation_alerts.v1', alerts: [alert] },
    'weekly/index.json': { schema_version: 'gids.situation_alerts.v1', periods: [week] },
    'weekly/2026-W45.json': week,
  };
  const files: Record<string, { url: string; sha256: string; bytes: number }> = {}, objects = new Map<string, string>();
  for (const [key, value] of Object.entries(entries)) {
    const body = JSON.stringify(value), sha256 = hash(body);
    files[key] = { url: `objects/${sha256}.json`, sha256, bytes: Buffer.byteLength(body) }; objects.set(sha256, body);
  }
  return { manifest: { schema_version: 1, release_id: `situation-${version}`, generated_at: day, files }, objects };
}

async function mock(page: Page) {
  let current = publication(1); const objects = new Map([...current.objects, ...publication(2).objects]);
  await page.route('**/site-data/situation/current.json', route => route.fulfill({ json: current.manifest }));
  await page.route('**/site-data/situation/objects/*.json', route => {
    const key = new URL(route.request().url()).pathname.split('/').at(-1)!.replace('.json', '');
    return route.fulfill({ body: objects.get(key), contentType: 'application/json' });
  });
  return { update() { current = publication(2); }, corrupt() { objects.set(current.manifest.files['map-data.json'].sha256, '{}'); } };
}

test('Situation JSON updates map and lazy timeline without rebuilding HTML; one page pins its release', async ({ page, request }) => {
  const publication = await mock(page);
  const before = hash(await (await request.get('/situation/')).body());
  await page.goto('/situation/?window=all', { waitUntil: 'networkidle' });
  await expect(page.locator('.sit-item').first()).toBeAttached();
  publication.update();
  const toggle = page.locator('.sit-bar-links button');
  if (await toggle.getAttribute('aria-pressed') === 'false') await toggle.click();
  await page.locator('.sit-item').first().click();
  await expect(page.locator('.sit-detail')).toContainText('Situation release 1.');
  await expect(page.locator('.sit-detail')).not.toContainText('Situation release 2.');
  await page.goto('/situation/?place=situation-update&window=all', { waitUntil: 'networkidle' });
  await expect(page.locator('.sit-detail')).toContainText('Situation release 2.');
  expect(hash(await (await request.get('/situation/')).body())).toBe(before);
});

test('country notices, homepage metrics and previously unbuilt weekly periods follow independent JSON', async ({ page, request }) => {
  const publication = await mock(page);
  const before = hash(await (await request.get('/countries/jp/')).body());
  await page.goto('/countries/jp/');
  await page.locator('#international').scrollIntoViewIfNeeded();
  await expect(page.locator('#international')).toContainText('Situation release 1.');
  publication.update();
  await page.goto('/countries/jp/');
  await page.locator('#international').scrollIntoViewIfNeeded();
  await expect(page.locator('#international')).toContainText('Situation release 2.');
  expect(hash(await (await request.get('/countries/jp/')).body())).toBe(before);
  await page.goto('/');
  await expect(page.locator('.situation-strip')).toContainText('2026-11-08');
  await page.goto('/situation/weekly/?period=2026-W45');
  await expect(page.locator('[data-situation-version="situation-2"]')).toContainText('2 alerts');
  await page.locator('main details summary').filter({ hasText: 'Influenza' }).click();
  await expect(page.locator('[data-situation-version="situation-2"]')).toContainText('Situation release 2.');
});

test('Situation integrity errors close reads instead of showing an older static snapshot', async ({ page }) => {
  const publication = await mock(page); publication.corrupt();
  await page.goto('/situation/');
  await expect(page.locator('.sit-item')).toHaveCount(0);
  await expect(page.locator('.sit-note')).toContainText('could not be loaded');
});
