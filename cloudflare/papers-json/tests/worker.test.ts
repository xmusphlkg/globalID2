import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import worker from '../src/index.ts';

const sha = (value: string) => createHash('sha256').update(value).digest('hex');
function fixture() {
  const keys: string[] = [], generation = 'a'.repeat(64), body = '<html>research landing</html>';
  const objects = new Map<string, string>();
  const objectKey = `releases/sha256/objects/${sha(body)}/0123456789abcdef`;
  const shards: Record<string, {key: string}> = {};
  for (const logical of ['research/', 'zh/research/', 'fr/research/', 'research/articles/paper-a/']) {
    const shard = JSON.stringify({ schema_version: 1, items: [{ logical_key: logical, key: objectKey, content_type: 'text/html' }] });
    const shardKey = `releases/sha256/catalogue-shards/${sha(shard)}.json`;
    objects.set(shardKey, shard); shards[sha(logical).slice(0, 2)] = { key: shardKey };
  }
  objects.set(objectKey, body);
  objects.set(`releases/sha256/catalogues/${generation}.json`, JSON.stringify({ schema_version: 2, generation_sha256: generation, shards }));
  objects.set('site-data/research-publication/current.json', JSON.stringify({ schema_version: 1, release_id: 'unified', papers: { papers_generation_sha256: generation } }));
  const get = async (key: string) => {
    keys.push(key); const value = objects.get(key);
    return value === undefined ? null : { size: Buffer.byteLength(value), body: new Response(value).body!, httpEtag: '"object"',
      json: async () => JSON.parse(value), text: async () => value, httpMetadata: {} };
  };
  return { env: { DATA: { get, head: get } } as unknown as Env, keys, generation, body };
}

test('research wrapper serves no-slash landing pages and frozen generation paths', async () => {
  const f = fixture();
  for (const path of ['/research', '/research/', '/zh/research', '/fr/research/', '/research/articles/paper-a/']) {
    const result = await worker.fetch(new Request(`https://papers.example${path}`), f.env);
    assert.equal(result.status, 200);
    assert.equal(result.headers.get('X-GIDS-Research-Release'), 'unified');
    assert.equal(await result.text(), f.body);
  }
  const versioned = await worker.fetch(new Request(`https://papers.example/research-publication/${f.generation}/research/`), f.env);
  assert.equal(versioned.status, 200); assert.match(versioned.headers.get('cache-control')!, /immutable/);
  assert.equal(await versioned.text(), f.body);
  assert.equal(f.keys.includes('paper-template-v2/current.json'), false);
});

test('research wrapper never activates surveillance or arbitrary R2 paths', async () => {
  const f = fixture();
  for (const path of ['/site-data/current.json', '/site-data/situation/current.json', '/site-data/updates/latest.json', '/private.json']) {
    assert.equal((await worker.fetch(new Request(`https://site.test${path}`), f.env)).status, 404);
  }
  assert.equal((await worker.fetch(new Request('https://site.test/research/', { method: 'POST' }), f.env)).status, 405);
  assert.equal(f.keys.length, 0);
});
