import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('real RSS Worker decodes published gzip R2 objects and follows Situation generations independently', async () => {
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, scriptPath: resolve('.wrangler/test-bundle/index.js'),
    compatibilityDate: '2026-11-07', compatibilityFlags: ['nodejs_compat'],
    r2Buckets: ['DATA'], bindings: { SITUATION_PREFIX: 'site-data/situation' },
  }));
  try {
    const bucket = await runtime.getR2Bucket('DATA');
    const url = 'https://site.test/site-data/situation/rss.xml?lang=zh';
    assert.equal((await runtime.dispatchFetch(url)).status, 503);
    const publish = async (identity, release) => {
      const value = JSON.stringify({ schema_version: 'gids.situation_alerts.v1', alerts: [{
        alert_id: identity, status: 'new', published_date: '2026-11-01', country_code: 'AU',
        country_name_en: 'Australia', country_name_zh: '澳大利亚', disease_id: 'D014',
        disease_name_en: 'Avian influenza', disease_name_zh: '禽流感',
        summary_zh: '官方动物疫情报告。'.repeat(150), host_type: 'animal', host_species: 'wild birds',
        url: 'https://who.example/original?one=1&two=2',
      }] });
      const sha = createHash('sha256').update(value).digest('hex');
      await bucket.put(`site-data/situation/objects/${sha}.json`, gzipSync(value),
        { httpMetadata: { contentEncoding: 'gzip', contentType: 'application/json' } });
      const empty = JSON.stringify({ schema_version: 'gids.situation_alerts.v1', alerts: [] });
      const emptySha = createHash('sha256').update(empty).digest('hex');
      await bucket.put(`site-data/situation/objects/${emptySha}.json`, empty);
      const descriptor = { url: `objects/${sha}.json`, sha256: sha, bytes: Buffer.byteLength(value) };
      await bucket.put('site-data/situation/current.json', JSON.stringify({ schema_version: 1, release_id: release,
        files: { 'feed.json': descriptor, 'by-disease/D014.json': descriptor,
          'by-country/US.json': { url: `objects/${emptySha}.json`, sha256: emptySha, bytes: Buffer.byteLength(empty) } } }));
    };
    await publish('first', 'situation-first');
    const first = await runtime.dispatchFetch(url);
    assert.equal(first.status, 200);
    assert.equal(first.headers.get('content-type'), 'application/rss+xml; charset=utf-8');
    assert.equal(first.headers.get('cache-control'), 'no-store');
    assert.equal(first.headers.get('x-gids-situation-release'), 'situation-first');
    assert.match(await first.text(), /gids:situation:first/);
    const unchanged = await runtime.dispatchFetch(url, { headers: { 'If-None-Match': first.headers.get('etag') } });
    assert.equal(unchanged.status, 304);
    await publish('second', 'situation-second');
    const changed = await runtime.dispatchFetch(url, { headers: { 'If-None-Match': first.headers.get('etag') } });
    assert.equal(changed.status, 200);
    assert.equal(changed.headers.get('x-gids-situation-release'), 'situation-second');
    assert.match(await changed.text(), /gids:situation:second/);
    const disease = await runtime.dispatchFetch(`${url}&disease=D014`);
    assert.match(await disease.text(), /禽流感/);
    const empty = await runtime.dispatchFetch(`${url}&country=US`);
    assert.equal(empty.status, 200);
    assert.doesNotMatch(await empty.text(), /<item>/);
    assert.equal((await runtime.dispatchFetch(`${url}&country=AU&disease=D014`)).status, 400);
    const head = await runtime.dispatchFetch(url, { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), '');
  } finally { await runtime.dispose(); }
});
