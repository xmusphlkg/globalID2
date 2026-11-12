import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { servePaper } from '../src/index.ts';
const hash = (data: string) => createHash('sha256').update(data).digest('hex');

function fixture() {
  const objects = new Map<string, string>();
  const put = (group: string, value: unknown) => {
    const raw = JSON.stringify(value), sha = hash(raw);
    objects.set(`paper-template-v1/${group}/${sha}.json`, raw);
    return sha;
  };
  const slug = 'paper-1';
  const record = put('records', { schema: 1, slug, head: ['<title>Paper</title>'], main: '<main id="main-content">Evidence</main>' });
  const template = put('templates', { schema: 1, chunks: ['<html><head>', { field: 'head', index: 0 }, '</head><body>', { field: 'main' }, '</body></html>'] });
  const index = put('indexes', { schema: 1, locale: 'en', items: { [slug]: record } });
  const generation = put('generations', { schema: 1, locales: { en: { template, indexes: { [hash(slug).slice(0,2)]: index } } } });
  objects.set('paper-template-v1/current.json', JSON.stringify({ schema: 1, generation }));
  objects.set('research/articles/paper-1/', '<html>legacy</html>');
  const keys: string[] = [];
  const env = { DATA: { async get(key: string) {
    keys.push(key); const raw = objects.get(key); if (raw === undefined) return null;
    const bytes = new TextEncoder().encode(raw);
    return { size: bytes.byteLength, httpMetadata: {}, body: new Blob([bytes]).stream(),
      async arrayBuffer() { return bytes.buffer; }, writeHttpMetadata(headers: Headers) { headers.set('Content-Type','text/html'); } };
  } } } as unknown as Env;
  return { env, objects, keys, generation, record, template };
}

test('serves complete SSR HTML, correct metadata and conditional requests', async () => {
  const { env } = fixture();
  const url='https://papers.example/research/articles/paper-1/';
  const res=await servePaper(new Request(url),env);
  assert.equal(res.status,200); assert.equal(res.headers.get('x-gids-paper-mode'),'template');
  assert.equal(await res.text(),'<html><head><title>Paper</title></head><body><main id="main-content">Evidence</main></body></html>');
  const etag=res.headers.get('etag')!;
  assert.equal((await servePaper(new Request(url,{headers:{'If-None-Match':etag}}),env)).status,304);
  const head=await servePaper(new Request(url,{method:'HEAD'}),env);
  assert.equal(head.status,200); assert.equal(await head.text(),'');
});

test('corrupted content falls back to the retained static page', async () => {
  const f=fixture(); f.objects.set(`paper-template-v1/records/${f.record}.json`, '{}');
  const res=await servePaper(new Request('https://papers.example/research/articles/paper-1/'),f.env);
  assert.equal(res.status,200); assert.equal(res.headers.get('x-gids-paper-mode'),'legacy');
  assert.equal(await res.text(),'<html>legacy</html>');
});

test('reads gzip JSON and validates the decoded content hash', async () => {
  const f=fixture(), original=f.env.DATA.get.bind(f.env.DATA);
  f.env.DATA.get = async (key: string) => {
    const item=await original(key);
    if (!item || !key.includes('/records/')) return item;
    const raw=new Uint8Array(await item.arrayBuffer());
    const compressed=gzipSync(raw);
    return { ...item, size: compressed.byteLength, httpMetadata: { contentEncoding: 'gzip' }, body: new Blob([compressed]).stream() } as R2ObjectBody;
  };
  const res=await servePaper(new Request('https://papers.example/research/articles/paper-1/'),f.env);
  assert.equal(res.status,200); assert.equal(res.headers.get('x-gids-paper-mode'),'template');
  assert.match(await res.text(), /<title>Paper<\/title>/);
});

test('does not expose arbitrary keys or accept writes', async () => {
  const f=fixture();
  assert.equal((await servePaper(new Request('https://papers.example/research/articles/../../secret'),f.env)).status,404);
  assert.equal((await servePaper(new Request('https://papers.example/research/articles/paper-1/',{method:'POST'}),f.env)).status,405);
  assert.equal(f.keys.length,0);
});
