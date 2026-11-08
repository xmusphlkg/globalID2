import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker, { objectPath } from '../src/index.ts';
import pages from '../src/pages.ts';

const hash = 'a'.repeat(64);
function fixture(content: string | null = '{"schema_version":1}') {
  const keys: string[] = [];
  const object = () => content === null ? null : {
    httpEtag: '"data-version"', size: new TextEncoder().encode(content).length,
    body: new Response(content).body!, json: async () => JSON.parse(content),
  };
  const env = { DATA_PREFIX: 'site-data/surveillance', DATA: {
    async get(key: string) { keys.push(key); return object(); },
    async head(key: string) { keys.push(key); const value = object();
      return value && { httpEtag: value.httpEtag, size: value.size }; },
  } };
  return { keys, env: env as Env };
}

test('same-origin pointer bypasses caching; immutable objects stream independently', async () => {
  const f = fixture();
  const pointer = await worker.fetch(new Request('https://site.test/site-data/current.json'), f.env);
  assert.equal(pointer.status, 200);
  assert.equal(pointer.headers.get('cache-control'), 'no-store');
  assert.equal(await pointer.text(), '{"schema_version":1}');
  const object = await worker.fetch(new Request(`https://site.test/site-data/objects/${hash}.json`), f.env);
  assert.match(object.headers.get('cache-control')!, /immutable/);
  assert.equal(object.headers.get('access-control-allow-origin'), '*');
  assert.deepEqual(f.keys, ['site-data/surveillance/current.json', `site-data/surveillance/objects/${hash}.json`]);
  assert.equal(await object.text(), '{"schema_version":1}');
});

test('missing initialization is never negatively cached and invalid paths do not access R2', async () => {
  const f = fixture(null);
  const missing = await worker.fetch(new Request('https://site.test/site-data/current.json'), f.env);
  assert.equal(missing.status, 404);
  assert.equal(missing.headers.get('cache-control'), 'no-store');
  for (const path of ['/site-data/objects/../secret.json', '/site-data/private/us.json', '/site-data/objects/a.json']) {
    assert.equal(objectPath(path), null);
    const response = await worker.fetch(new Request(`https://site.test${path}`), f.env);
    assert.equal(response.status, 404);
  }
  assert.equal(f.keys.length, 1);
});

test('HEAD and conditional reads preserve the version ETag without exposing a body', async () => {
  const f = fixture();
  const head = await worker.fetch(new Request(`https://site.test/site-data/objects/${hash}.json`, { method: 'HEAD' }), f.env);
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  const cached = await worker.fetch(new Request('https://site.test/site-data/current.json', {
    headers: { 'If-None-Match': '"other", "data-version"' },
  }), f.env);
  assert.equal(cached.status, 304);
  assert.equal(cached.headers.get('cache-control'), 'no-store');
  assert.equal(await cached.text(), '');
});

test('read-only methods do not mutate or accidentally proxy to the static site', async () => {
  const f = fixture();
  const response = await worker.fetch(new Request('https://site.test/site-data/current.json', { method: 'POST' }), f.env);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('allow'), 'GET, HEAD, OPTIONS');
  assert.equal(f.keys.length, 0);
});

test('legacy logical JSON URLs resolve through current instead of stale Pages files', async () => {
  const f = fixture(JSON.stringify({ schema_version: 1, files: {
    'countries/us.json': { url: `objects/${hash}.json`, sha256: hash },
  } }));
  const response = await worker.fetch(new Request('https://site.test/site-data/countries/us.json'), f.env);
  assert.equal(response.status, 307);
  assert.equal(response.headers.get('location'), `/site-data/objects/${hash}.json`);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual(f.keys, ['site-data/surveillance/current.json']);
});

test('Situation uses an independent pointer, immutable namespace and legacy aliases', async () => {
  const f = fixture(JSON.stringify({ schema_version: 1, files: { 'map-data.json': { url: `objects/${hash}.json`, sha256: hash } } }));
  const pointer = await worker.fetch(new Request('https://site.test/site-data/situation/current.json'), f.env);
  assert.equal(pointer.status, 200);
  assert.equal(pointer.headers.get('cache-control'), 'no-store');
  const alias = await worker.fetch(new Request('https://site.test/situation/map-data.json'), f.env);
  assert.equal(alias.status, 307);
  assert.equal(alias.headers.get('location'), `/site-data/situation/objects/${hash}.json`);
  const object = await worker.fetch(new Request(`https://site.test/site-data/situation/objects/${hash}.json`), f.env);
  assert.equal(object.status, 200);
  assert.match(object.headers.get('cache-control')!, /immutable/);
  assert.deepEqual(f.keys, ['site-data/situation/current.json', 'site-data/situation/current.json', `site-data/situation/objects/${hash}.json`]);
  assert.equal((await worker.fetch(new Request('https://site.test/site-data/situation/objects/bad.json'), f.env)).status, 404);
});

test('Pages Advanced Mode preserves static routes and serves JSON through the same handler', async () => {
  const f = fixture();
  const assets: string[] = [];
  const env = { ...f.env, ASSETS: { async fetch(request: Request) { assets.push(request.url); return new Response('static asset'); } } };
  assert.equal(await (await pages.fetch(new Request('https://site.test/countries/jp/'), env)).text(), 'static asset');
  assert.equal(await (await pages.fetch(new Request('https://site.test/site-data/search-index.json'), env)).text(), 'static asset');
  assert.equal((await pages.fetch(new Request('https://site.test/site-data/current.json'), env)).status, 200);
  assert.equal((await pages.fetch(new Request('https://site.test/site-data/situation/current.json'), env)).status, 200);
  assert.deepEqual(assets, ['https://site.test/countries/jp/', 'https://site.test/site-data/search-index.json']);
  assert.deepEqual(f.keys, ['site-data/surveillance/current.json', 'site-data/situation/current.json']);
});

test('public update files use a restricted namespace and are never cached', async () => {
  const f = fixture();
  for (const name of ['latest.json', '2026-11.json']) {
    const response = await worker.fetch(new Request(`https://site.test/site-data/updates/${name}`), f.env);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('cache-control'), 'no-store');
  }
  for (const name of ['private.json', '2026-13.json', '2026-00.json']) {
    assert.equal((await worker.fetch(new Request(`https://site.test/site-data/updates/${name}`), f.env)).status, 404);
  }
  assert.deepEqual(f.keys, ['site-data/updates/latest.json', 'site-data/updates/2026-11.json']);
});
