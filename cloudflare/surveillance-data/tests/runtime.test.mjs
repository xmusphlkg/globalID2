import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { gzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { buildPages } from '../scripts/build-pages.mjs';

test('real local Worker streams R2 generations and exposes a fresh same-origin pointer', async () => {
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, scriptPath: resolve('.wrangler/test-bundle/index.js'),
    compatibilityDate: '2026-11-07', compatibilityFlags: ['nodejs_compat'],
    r2Buckets: ['DATA'], bindings: { DATA_PREFIX: 'site-data/surveillance' },
  }));
  try {
    const bucket = await runtime.getR2Bucket('DATA');
    const origin = 'https://globalinfectiousdisease.com';
    const absent = await runtime.dispatchFetch(`${origin}/site-data/current.json`);
    assert.equal(absent.status, 404);
    assert.equal(absent.headers.get('cache-control'), 'no-store');
    const first = 'a'.repeat(64), second = 'b'.repeat(64);
    await bucket.put(`site-data/surveillance/objects/${first}.json`, '{"cases":1}');
    await bucket.put('site-data/surveillance/current.json', JSON.stringify({ schema_version: 1, release_id: 'one',
      files: { 'countries/us.json': { url: `objects/${first}.json`, sha256: first } } }));
    const pointer = await runtime.dispatchFetch(`${origin}/site-data/current.json`);
    assert.equal((await pointer.json()).release_id, 'one');
    assert.equal(pointer.headers.get('cache-control'), 'no-store');
    await bucket.put(`site-data/surveillance/objects/${second}.json`, gzipSync('{"cases":2}'),
      { httpMetadata: { contentEncoding: 'gzip', contentType: 'application/json' } });
    await bucket.put('site-data/surveillance/current.json', JSON.stringify({ schema_version: 1, release_id: 'two',
      files: { 'countries/us.json': { url: `objects/${second}.json`, sha256: second } } }));
    assert.equal((await (await runtime.dispatchFetch(`${origin}/site-data/current.json`)).json()).release_id, 'two');
    const retained = await runtime.dispatchFetch(`${origin}/site-data/objects/${first}.json`);
    assert.deepEqual(await retained.json(), { cases: 1 });
    assert.match(retained.headers.get('cache-control'), /immutable/);
    // Actual HTTP fetch performs gzip decoding just like a browser; the Worker
    // keeps R2 bytes streaming and forwards Content-Encoding.
    const address = await runtime.ready;
    const updated = await fetch(new URL(`/site-data/objects/${second}.json`, address));
    assert.deepEqual(await updated.json(), { cases: 2 });
    assert.equal(updated.headers.get('content-encoding'), 'gzip');
    const alias = await runtime.dispatchFetch(`${origin}/site-data/countries/us.json`, { redirect: 'manual' });
    assert.equal(alias.status, 307);
    assert.equal(alias.headers.get('location'), `/site-data/objects/${second}.json`);
    const head = await runtime.dispatchFetch(`${origin}/site-data/objects/${first}.json`, { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), '');
    await bucket.put(`site-data/situation/objects/${first}.json`, '{"points":[]}');
    await bucket.put('site-data/situation/current.json', JSON.stringify({ schema_version: 1, release_id: 'situation-one',
      files: { 'map-data.json': { url: `objects/${first}.json`, sha256: first } } }));
    assert.equal((await (await runtime.dispatchFetch(`${origin}/site-data/situation/current.json`)).json()).release_id, 'situation-one');
    assert.equal((await (await runtime.dispatchFetch(`${origin}/site-data/current.json`)).json()).release_id, 'two');
    const situationAlias = await runtime.dispatchFetch(`${origin}/situation/map-data.json`, { redirect: 'manual' });
    assert.equal(situationAlias.headers.get('location'), `/site-data/situation/objects/${first}.json`);
    assert.deepEqual(await (await runtime.dispatchFetch(`${origin}/site-data/situation/objects/${first}.json`)).json(), { points: [] });
    await bucket.put(`site-data/research-publication/objects/${first}.json`, '{"articles":[]}');
    await bucket.put('site-data/research-publication/current.json', JSON.stringify({ schema_version: 1, release_id: 'research-one',
      files: { 'index.json': { url: `objects/${first}.json`, sha256: first } } }));
    assert.equal((await (await runtime.dispatchFetch(`${origin}/site-data/research-publication/current.json`)).json()).release_id, 'research-one');
    assert.deepEqual(await (await runtime.dispatchFetch(`${origin}/site-data/research-publication/objects/${first}.json`)).json(), { articles: [] });
    assert.equal((await (await runtime.dispatchFetch(`${origin}/site-data/current.json`)).json()).release_id, 'two');
    const logical = 'research/articles/example/';
    const paperKey = `releases/sha256/objects/${first}/0123456789abcdef`;
    const cssKey = `releases/sha256/objects/${second}/0123456789abcdef`;
    const document = '<html><head><link rel="canonical" href="https://papers.globalinfectiousdisease.com/research/articles/example/"><link rel="stylesheet" href="/_astro/style.css"></head><body><a href="/research/">paper</a></body></html>';
    await bucket.put(paperKey, document, { httpMetadata: { contentType: 'text/html' } });
    await bucket.put(cssKey, 'body{src:url(/_astro/font.woff2)}', { httpMetadata: { contentType: 'text/css' } });
    const groups = {};
    for (const entry of [{ logical_key: logical, key: paperKey, content_type: 'text/html' },
      { logical_key: '_astro/style.css', key: cssKey, content_type: 'text/css' }]) {
      const group = createHash('sha256').update(entry.logical_key).digest('hex').slice(0, 2);
      (groups[group] ??= []).push(entry);
    }
    const shards = {};
    for (const [group, items] of Object.entries(groups)) {
      const shardKey = `releases/sha256/catalogue-shards/${group.repeat(32)}.json`;
      shards[group] = { key: shardKey };
      await bucket.put(shardKey, JSON.stringify({ schema_version: 1, items }));
    }
    await bucket.put(`releases/sha256/catalogues/${first}.json`, JSON.stringify({ schema_version: 2, generation_sha256: first, shards }));
    const paper = await runtime.dispatchFetch(`${origin}/research-publication/${first}/${logical}`);
    const html = await paper.text();
    assert.match(html, new RegExp(`/research-publication/${first}/_astro/style.css`));
    assert.match(html, new RegExp(`/research-publication/${first}/research/`));
    assert.match(html, /rel="canonical" href="https:\/\/papers.globalinfectiousdisease.com\/research\/articles\/example\/"/);
    assert.match(paper.headers.get('cache-control'), /immutable/);
    const css = await runtime.dispatchFetch(`${origin}/research-publication/${first}/_astro/style.css`);
    assert.match(await css.text(), new RegExp(`/research-publication/${first}/_astro/font.woff2`));
    await bucket.put('site-data/research-publication/current.json', JSON.stringify({ schema_version: 1, release_id: 'research-one',
      papers: { papers_generation_sha256: first }, files: {} }));
    const stable = await runtime.dispatchFetch(`${origin}/${logical}`, { headers: { 'If-None-Match': paper.headers.get('etag') } });
    assert.equal(stable.status, 200);
    assert.equal(stable.headers.get('cache-control'), 'no-store');
    assert.equal(stable.headers.get('etag'), null);
    assert.equal(stable.headers.get('x-gids-research-release'), 'research-one');
    assert.match(await stable.text(), new RegExp(`<meta name="gids-research-generation" content="${first}">`));
    const papersHost = await runtime.dispatchFetch(`https://papers.globalinfectiousdisease.com/${logical}`);
    assert.match(await papersHost.text(), new RegExp(`https://globalinfectiousdisease.com/research-publication/${first}/_astro/style.css`));
  } finally { await runtime.dispose(); }
});

test('packaged Pages Worker routes live JSON while preserving ordinary static assets', async () => {
  const directory = mkdtempSync(join(tmpdir(), 'gids-pages-data-'));
  await buildPages(directory, { researchEnabled: false });
  assert.equal(JSON.parse(readFileSync(join(directory, '_routes.json'), 'utf8')).include.includes('/research/*'), false);
  await buildPages(directory, { researchEnabled: true });
  const routes = JSON.parse(readFileSync(join(directory, '_routes.json'), 'utf8'));
  assert.equal(routes.include.includes('/*'), false);
  assert.equal(routes.include.includes('/site-data/situation/*'), true);
  assert.equal(routes.include.includes('/site-data/research-publication/*'), true);
  assert.equal(routes.include.includes('/research-publication/*'), true);
  assert.equal(routes.include.includes('/research/*'), true);
  assert.equal(routes.include.includes('/site-data/updates/*'), true);
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, rootPath: directory, scriptPath: join(directory, '_worker.js'), compatibilityDate: '2026-11-07',
    compatibilityFlags: ['nodejs_compat'], r2Buckets: ['DATA'],
    serviceBindings: { ASSETS: request => new Response(`static:${new URL(request.url).pathname}`) },
  }));
  try {
    const bucket = await runtime.getR2Bucket('DATA');
    await bucket.put('site-data/situation/current.json', '{"schema_version":1,"release_id":"pages-situation","files":{}}');
    assert.equal((await (await runtime.dispatchFetch('https://site.test/site-data/situation/current.json')).json()).release_id, 'pages-situation');
    const updateUrl = 'https://site.test/site-data/updates/latest.json';
    const missing = await runtime.dispatchFetch(updateUrl);
    assert.equal(missing.status, 404);
    assert.equal(missing.headers.get('cache-control'), 'no-store');
    await bucket.put('site-data/updates/latest.json', '{"schema_version":1,"months":["2026-11"]}');
    const index = await runtime.dispatchFetch(updateUrl);
    assert.deepEqual((await index.json()).months, ['2026-11']);
    assert.equal(index.headers.get('cache-control'), 'no-store');
    const unchanged = await runtime.dispatchFetch(updateUrl, { headers: { 'If-None-Match': index.headers.get('etag') } });
    assert.equal(unchanged.status, 304);
    assert.equal(unchanged.headers.get('cache-control'), 'no-store');
    await bucket.put('site-data/updates/2026-11.json', '{"schema_version":1,"month":"2026-11","events":[]}');
    const monthHead = await runtime.dispatchFetch('https://site.test/site-data/updates/2026-11.json', { method: 'HEAD' });
    assert.equal(monthHead.status, 200);
    assert.equal(await monthHead.text(), '');
    assert.equal(monthHead.headers.get('cache-control'), 'no-store');
    assert.equal(await (await runtime.dispatchFetch('https://site.test/countries/jp/')).text(), 'static:/countries/jp/');
    assert.equal(await (await runtime.dispatchFetch('https://site.test/site-data/search-index.json')).text(), 'static:/site-data/search-index.json');
  } finally { await runtime.dispose(); rmSync(directory, { recursive: true, force: true }); }
});
