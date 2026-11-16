import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import worker from '../src/index.ts';
import pages from '../src/pages.ts';
import { renderSituationFeed, situationFeedSelection } from '../src/situation-feed.ts';

const alert = {
  alert_id: 'notice-one', status: 'new', published_date: '2026-11-01',
  disease_id: 'D014', disease_name_en: 'Avian influenza', disease_name_zh: '禽流感',
  country_code: 'AU', country_name_en: 'Australia', country_name_zh: '澳大利亚',
  source_label_en: 'WHO & partners', source_label_zh: '世卫组织',
  summary_en: 'Report <one> & "quoted" \u0001\uD800 🐦', summary_zh: '官方动物疫情通报', summary_fr: 'Rapport animal officiel',
  url: 'https://who.example/report?one=1&two=2', host_type: 'animal', host_species: 'wild birds',
};
const document = (alerts: unknown[] = [alert]) => ({ schema_version: 'gids.situation_alerts.v1', alerts });

function fixture(payload: unknown = document(), logical = 'feed.json', compressed = false) {
  const body = JSON.stringify(payload);
  const sha = createHash('sha256').update(body).digest('hex');
  const stored = new Map([
    ['site-data/situation/current.json', JSON.stringify({ schema_version: 1, release_id: 'situation-one',
      files: { [logical]: { url: `objects/${sha}.json`, sha256: sha, bytes: Buffer.byteLength(body) } } })],
    [`site-data/situation/objects/${sha}.json`, body],
  ]);
  const reads: string[] = [];
  const env = { DATA: { async get(key: string) {
    reads.push(key);
    const value = stored.get(key);
    if (value === undefined) return null;
    const wire = compressed && key.includes('/objects/') ? gzipSync(value) : value;
    return { size: Buffer.byteLength(wire),
      httpMetadata: { contentEncoding: typeof wire === 'string' ? undefined : 'gzip' },
      body: new Response(wire).body! };
  } } } as Env;
  return { env, reads, stored, sha };
}

test('feed filters permit one known selection and reject duplicates, combinations and arbitrary keys', () => {
  for (const query of ['lang=de', 'country=AU&disease=D014', 'country=../secret', 'disease=unknown', 'lang=zh&lang=en', 'token=secret', 'country=']) {
    assert.equal(situationFeedSelection(new URL(`https://site.test/site-data/situation/rss.xml?${query}`)), null);
  }
  assert.deepEqual(situationFeedSelection(new URL('https://site.test/site-data/situation/rss.xml?lang=zh&country=ca-on')),
    { language: 'zh', country: 'CA-ON' });
});

test('RSS escapes XML, removes forbidden characters, retains dates, source links and animal labels', () => {
  const body = renderSituationFeed(document(), 'https://site.test', { language: 'en' });
  assert.match(body, /Report &lt;one&gt; &amp; &quot;quoted&quot;/);
  assert.doesNotMatch(body, /[\u0001\uD800]/u);
  assert.match(body, /🐦/u);
  assert.match(body, /<link>https:\/\/who.example\/report\?one=1&amp;two=2<\/link>/);
  assert.match(body, /Animal: wild birds/);
  assert.match(body, /WHO &amp; partners/);
  assert.match(body, /<pubDate>Sun, 01 Nov 2026 00:00:00 GMT<\/pubDate>/);
  assert.match(body, /<guid isPermaLink="false">gids:situation:notice-one<\/guid>/);
  const legacy = renderSituationFeed(document([{ ...alert, host_type: undefined, host_species: undefined, url: 'javascript:alert(1)' }]),
    'https://site.test', { language: 'fr' });
  assert.match(legacy, /Hôte non précisé/);
  assert.match(legacy, /<link>https:\/\/site.test\/fr\/situation\/<\/link>/);
  assert.doesNotMatch(legacy, /javascript:/);
});

test('RSS sorts, deduplicates, excludes routine notices, filters before limiting and caps the result', () => {
  const rows = Array.from({ length: 130 }, (_, i) => ({ ...alert, alert_id: `id-${String(i).padStart(3, '0')}` }));
  rows.push({ ...alert, alert_id: 'routine', status: 'routine' });
  rows.push({ ...alert, alert_id: 'other-country', country_code: 'US' });
  rows.push({ ...alert, alert_id: 'id-129' });
  const body = renderSituationFeed(document(rows), 'https://site.test', { language: 'zh', country: 'AU' });
  assert.equal((body.match(/<item>/g) || []).length, 100);
  assert.match(body, /禽流感 · 澳大利亚 · 新发通报/);
  assert.doesNotMatch(body, /gids:situation:routine|gids:situation:other-country|gids:situation:id-000/);
  assert.equal((body.match(/gids:situation:id-129/g) || []).length, 1);
  assert.ok(body.indexOf('gids:situation:id-129') < body.indexOf('gids:situation:id-128'));
  assert.match(body, /lang=zh&amp;country=AU/);
  assert.throws(() => renderSituationFeed(document([{ ...alert, published_date: '2026-02-31' }]), 'https://site.test', { language: 'en' }));
});

test('RSS follows the latest independent generation without a static rebuild and uses two pinned reads', async () => {
  const f = fixture();
  const url = 'https://site.test/site-data/situation/rss.xml';
  const first = await worker.fetch(new Request(url), f.env);
  assert.equal(first.status, 200);
  assert.equal(first.headers.get('content-type'), 'application/rss+xml; charset=utf-8');
  assert.equal(first.headers.get('cache-control'), 'no-store');
  assert.equal(first.headers.get('x-gids-situation-release'), 'situation-one');
  assert.deepEqual(f.reads, ['site-data/situation/current.json', `site-data/situation/objects/${f.sha}.json`]);
  assert.match(await first.text(), /notice-one/);
  const body = JSON.stringify(document([{ ...alert, alert_id: 'notice-two' }]));
  const sha = createHash('sha256').update(body).digest('hex');
  f.stored.set(`site-data/situation/objects/${sha}.json`, body);
  f.stored.set('site-data/situation/current.json', JSON.stringify({ schema_version: 1, release_id: 'situation-two',
    files: { 'feed.json': { url: `objects/${sha}.json`, sha256: sha, bytes: Buffer.byteLength(body) } } }));
  const second = await pages.fetch(new Request(url, { headers: { 'If-None-Match': first.headers.get('etag')! } }), f.env);
  assert.equal(second.status, 200);
  assert.equal(second.headers.get('x-gids-situation-release'), 'situation-two');
  assert.match(await second.text(), /notice-two/);
  assert.notEqual(second.headers.get('etag'), first.headers.get('etag'));
});

test('RSS supports HEAD, conditional requests and read-only methods', async () => {
  const f = fixture();
  const url = 'https://site.test/site-data/situation/rss.xml';
  const response = await worker.fetch(new Request(url), f.env);
  const cached = await worker.fetch(new Request(url, { headers: { 'If-None-Match': response.headers.get('etag')! } }), f.env);
  assert.equal(cached.status, 304);
  assert.equal(await cached.text(), '');
  const head = await worker.fetch(new Request(url, { method: 'HEAD' }), f.env);
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  const reads = f.reads.length;
  assert.equal((await worker.fetch(new Request(url, { method: 'POST' }), f.env)).status, 405);
  assert.equal((await worker.fetch(new Request(url, { method: 'OPTIONS' }), f.env)).status, 204);
  assert.equal((await worker.fetch(new Request(`${url}?disease=D014&country=AU`), f.env)).status, 400);
  assert.equal(f.reads.length, reads);
});

test('entity feeds read their own complete shard and valid empty differs from missing publication', async () => {
  for (const [query, logical] of [['country=AU', 'by-country/AU.json'], ['disease=D014', 'by-disease/D014.json']]) {
    const f = fixture(document([]), logical);
    const response = await worker.fetch(new Request(`https://site.test/site-data/situation/rss.xml?lang=fr&${query}`), f.env);
    assert.equal(response.status, 200);
    assert.equal((await response.text()).includes('<item>'), false);
    assert.equal(f.reads.length, 2);
  }
  const f = fixture();
  f.stored.delete('site-data/situation/current.json');
  const missing = await worker.fetch(new Request('https://site.test/site-data/situation/rss.xml'), f.env);
  assert.equal(missing.status, 503);
  assert.equal(missing.headers.get('cache-control'), 'no-store');
  assert.equal(missing.headers.get('retry-after'), '300');
  assert.doesNotMatch(await missing.text(), /<rss/);
});

test('missing feed, corrupt immutable bytes and unsupported sizes fail closed', async () => {
  const url = 'https://site.test/site-data/situation/rss.xml';
  const absent = fixture(document(), 'unrelated.json');
  assert.equal((await worker.fetch(new Request(url), absent.env)).status, 503);
  const corrupt = fixture();
  corrupt.stored.set(`site-data/situation/objects/${corrupt.sha}.json`, JSON.stringify(document([])));
  assert.equal((await worker.fetch(new Request(url), corrupt.env)).status, 503);
  const oversized = fixture();
  const manifest = JSON.parse(oversized.stored.get('site-data/situation/current.json')!);
  manifest.files['feed.json'].bytes = 8 * 1024 * 1024 + 1;
  oversized.stored.set('site-data/situation/current.json', JSON.stringify(manifest));
  assert.equal((await worker.fetch(new Request(url), oversized.env)).status, 503);
  assert.equal(oversized.reads.length, 1);
});

test('published gzip objects are bounded after decompression and verified against the uncompressed digest', async () => {
  const f = fixture(document(), 'feed.json', true);
  const response = await worker.fetch(new Request('https://site.test/site-data/situation/rss.xml'), f.env);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /Avian influenza/);
  const huge = fixture(document(), 'feed.json', true);
  huge.stored.set(`site-data/situation/objects/${huge.sha}.json`, ' '.repeat(8 * 1024 * 1024 + 1));
  const rejected = await worker.fetch(new Request('https://site.test/site-data/situation/rss.xml'), huge.env);
  assert.equal(rejected.status, 503);
});
