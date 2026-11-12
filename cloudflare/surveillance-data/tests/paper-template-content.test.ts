import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { test } from 'node:test';
import worker from '../src/index.ts';

const sha = (value: string | Uint8Array) => createHash('sha256').update(value).digest('hex');

function fixture() {
  const objects = new Map<string, { bytes: Uint8Array; gzip?: boolean }>();
  const reads: string[] = [];
  const put = (key: string, value: unknown, compressed = false) => {
    const bytes = Buffer.from(typeof value === 'string' ? value : JSON.stringify(value));
    objects.set(key, { bytes: compressed ? gzipSync(bytes) : bytes, gzip: compressed });
  };
  const get = async (key: string) => {
    reads.push(key);
    const value = objects.get(key);
    if (!value) return null;
    return { size: value.bytes.length, body: new Response(value.bytes).body!,
      httpMetadata: value.gzip ? { contentEncoding: 'gzip' } : {}, httpEtag: `"${sha(value.bytes)}"`,
      text: async () => new TextDecoder().decode(value.bytes),
      json: async () => JSON.parse(new TextDecoder().decode(value.bytes)) };
  };
  const env = { DATA: { get, head: get } } as unknown as Env;
  const record = { schema: 2, slug: 'paper-a', locales: {
    en: { head: ['<title>English paper</title>'], main: '<main>English</main>' },
    zh: { head: ['<title>中文论文</title>'], main: '<main>中文</main>' },
    fr: { head: ['<title>Article français</title>'], main: '<main>Français</main>' },
  } };
  const recordHash = sha(JSON.stringify(record));
  const recordKey = `paper-template-v2/records/${recordHash}.json`;
  put(recordKey, record, true);
  function generation(version: string) {
    const template = { schema: 1, chunks: ['<html><head>', { field: 'head', index: 0 },
      `</head><body>`, { field: 'main' }, `<footer>${version}</footer></body></html>`] };
    const templateHash = sha(JSON.stringify(template));
    const templateKey = `paper-template-v2/templates/${templateHash}.json`;
    put(templateKey, template, true);
    const expected: Record<string, string> = {};
    const descriptors: Record<string, { key: string }> = {};
    const entries: Array<Record<string, unknown>> = [];
    for (const locale of ['en', 'zh', 'fr'] as const) {
      const logical = `${locale === 'en' ? '' : `${locale}/`}research/articles/paper-a/`;
      const html = `<html><head>${record.locales[locale].head[0]}</head><body>${record.locales[locale].main}<footer>${version}</footer></body></html>`;
      expected[logical] = html;
      const entry = { logical_key: logical, key: recordKey, content_type: 'text/html; charset=utf-8',
        sha256: sha(html), bytes: Buffer.byteLength(html),
        paper_template: { template_sha256: templateHash, record_sha256: recordHash, locale, slug: 'paper-a' } };
      entries.push(entry);
      const shard = { schema_version: 1, items: [entry] };
      const shardKey = `releases/sha256/catalogue-shards/${sha(JSON.stringify(shard))}.json`;
      descriptors[sha(logical).slice(0, 2)] = { key: shardKey };
      put(shardKey, shard);
    }
    const hash = sha(version);
    put(`releases/sha256/catalogues/${hash}.json`, { schema_version: 2, generation_sha256: hash, shards: descriptors });
    const activate = () => put('site-data/research-publication/current.json', {
      schema_version: 1, release_id: version, papers: { papers_generation_sha256: hash }, files: {},
    });
    return { hash, expected, entries, templateKey, activate };
  }
  return { objects, reads, env, put, generation, recordKey };
}

test('one publication pointer switches all locale templates while old versioned pages remain readable', async () => {
  const f = fixture();
  const first = f.generation('first'); first.activate();
  for (const [logical, html] of Object.entries(first.expected)) {
    const response = await worker.fetch(new Request(`https://papers.globalinfectiousdisease.com/${logical}`), f.env);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('X-GIDS-Paper-Mode'), 'template');
    assert.equal(response.headers.get('X-GIDS-Paper-SHA256'), sha(html));
    assert.equal(response.headers.get('X-GIDS-Research-Release'), 'first');
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.equal(await response.text(), html);
  }
  const next = f.generation('second'); next.activate();
  const logical = 'research/articles/paper-a/';
  const current = await worker.fetch(new Request(`https://site.test/${logical}`), f.env);
  assert.equal(await current.text(), next.expected[logical]);
  const oldUrl = `https://site.test/research-publication/${first.hash}/${logical}`;
  const old = await worker.fetch(new Request(oldUrl), f.env);
  assert.equal(await old.text(), first.expected[logical]);
  assert.match(old.headers.get('Cache-Control')!, /immutable/);
  const conditional = await worker.fetch(new Request(oldUrl, { headers: { 'If-None-Match': old.headers.get('ETag')! } }), f.env);
  assert.equal(conditional.status, 304);
  const head = await worker.fetch(new Request(oldUrl, { method: 'HEAD' }), f.env);
  assert.equal(head.status, 200); assert.equal(await head.text(), '');
  assert.equal(f.reads.some(key => key === 'paper-template-v2/current.json'), false);
});

test('template and record are cached immutably, but every canonical request reads current', async t => {
  const cached = new Map<string, Response>();
  const original = Object.getOwnPropertyDescriptor(globalThis, 'caches');
  Object.defineProperty(globalThis, 'caches', { configurable: true, value: { default: {
    async match(request: Request) { return cached.get(request.url)?.clone(); },
    async put(request: Request, response: Response) { cached.set(request.url, response.clone()); },
  } } });
  t.after(() => { if (original) Object.defineProperty(globalThis, 'caches', original); else delete (globalThis as any).caches; });
  const f = fixture(), first = f.generation('cache'); first.activate();
  const url = 'https://site.test/research/articles/paper-a/';
  assert.equal((await worker.fetch(new Request(url), f.env)).status, 200);
  assert.equal((await worker.fetch(new Request(url), f.env)).status, 200);
  assert.equal(f.reads.filter(key => key === f.recordKey).length, 1);
  assert.equal(f.reads.filter(key => key === first.templateKey).length, 1);
  assert.equal(f.reads.filter(key => key === 'site-data/research-publication/current.json').length, 2);
});

test('corrupt immutable records fail closed instead of silently serving old static papers', async () => {
  const f = fixture(), first = f.generation('corrupt'); first.activate();
  f.put(f.recordKey, { schema: 2, slug: 'wrong' }, true);
  const response = await worker.fetch(new Request('https://site.test/research/articles/paper-a/'), f.env);
  assert.equal(response.status, 503);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(f.reads.includes('research/articles/paper-a/'), false);
});

test('wrong assembled page checksum is rejected even if template and record hashes are valid', async () => {
  const f = fixture(), first = f.generation('mismatch'); first.activate();
  const entry = { ...first.entries[0], sha256: '0'.repeat(64) };
  const shard = { schema_version: 1, items: [entry] }, logical = entry.logical_key as string;
  const key = `releases/sha256/catalogue-shards/${sha(JSON.stringify(shard))}.json`;
  f.put(key, shard);
  f.put(`releases/sha256/catalogues/${first.hash}.json`, { schema_version: 2, generation_sha256: first.hash,
    shards: { [sha(logical).slice(0, 2)]: { key } } });
  assert.equal((await worker.fetch(new Request(`https://site.test/${logical}`), f.env)).status, 503);
});
