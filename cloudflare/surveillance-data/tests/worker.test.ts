import assert from 'node:assert/strict';
import { test } from 'node:test';
import worker, { objectPath } from '../src/index.ts';
import pages from '../src/pages.ts';
import { createHash } from 'node:crypto';

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

test('research publication is isolated and exposes only its pointer and immutable objects', async () => {
  const f = fixture();
  const pointer = await worker.fetch(new Request('https://site.test/site-data/research-publication/current.json'), f.env);
  assert.equal(pointer.status, 200);
  assert.equal(pointer.headers.get('cache-control'), 'no-store');
  const object = await worker.fetch(new Request(`https://site.test/site-data/research-publication/objects/${hash}.json`), f.env);
  assert.equal(object.status, 200);
  assert.match(object.headers.get('cache-control')!, /immutable/);
  assert.equal((await worker.fetch(new Request('https://site.test/site-data/research-publication/private.json'), f.env)).status, 404);
  assert.deepEqual(f.keys, ['site-data/research-publication/current.json', `site-data/research-publication/objects/${hash}.json`]);
});

test('versioned research pages resolve through the pinned catalogue and stream immutable content', async () => {
  const generation = 'b'.repeat(64), shard = 'c'.repeat(64), content = 'd'.repeat(64);
  const logical = 'research/articles/example/', group = createHash('sha256').update(logical).digest('hex').slice(0, 2);
  const shardKey = `releases/sha256/catalogue-shards/${shard}.json`;
  const objectKey = `releases/sha256/objects/${content}/0123456789abcdef`;
  const stored = new Map([
    [`releases/sha256/catalogues/${generation}.json`, JSON.stringify({ schema_version: 2, generation_sha256: generation, shards: { [group]: { key: shardKey } } })],
    [shardKey, JSON.stringify({ schema_version: 1, items: [{ logical_key: logical, key: objectKey, content_type: 'text/html; charset=utf-8' }] })],
    [objectKey, '<html>paper</html>'],
  ]);
  const keys: string[] = [];
  const get = async (key: string) => {
    keys.push(key); const body = stored.get(key);
    return body === undefined ? null : { size: Buffer.byteLength(body), httpEtag: '"paper"',
      body: new Response(body).body, json: async () => JSON.parse(body), text: async () => body };
  };
  const env = { DATA: { get, head: get } } as unknown as Env;
  const url = `https://site.test/research-publication/${generation}/${logical}`;
  const response = await worker.fetch(new Request(url), env);
  assert.equal(await response.text(), '<html>paper</html>');
  assert.equal(response.headers.get('content-type'), 'text/html; charset=utf-8');
  assert.match(response.headers.get('cache-control')!, /immutable/);
  assert.deepEqual(keys, [`releases/sha256/catalogues/${generation}.json`, shardKey, objectKey]);
  const cached = await worker.fetch(new Request(url, { headers: { 'If-None-Match': '"paper"' } }), env);
  assert.equal(cached.status, 304);
  assert.equal((await worker.fetch(new Request(url, { method: 'POST' }), env)).status, 405);
  assert.equal((await worker.fetch(new Request(`https://site.test/research-publication/${generation}/missing`), env)).status, 404);
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
