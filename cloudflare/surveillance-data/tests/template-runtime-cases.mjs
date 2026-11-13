import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { readFileSync, readdirSync } from 'node:fs';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('template v2 catalogue entries fail closed and snapshot page URLs stay removed', async () => {
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, scriptPath: resolve('.wrangler/test-bundle/index.js'),
    compatibilityDate: '2026-11-07', compatibilityFlags: ['nodejs_compat'], r2Buckets: ['DATA'],
  }));
  const sha = value => createHash('sha256').update(value).digest('hex');
  try {
    const bucket = await runtime.getR2Bucket('DATA');
    const origin = 'https://globalinfectiousdisease.com', logical = 'research/articles/example/';
    const record = { schema: 2, slug: 'example', locales: { en: {
      head: ['<title>Paper</title><link rel="canonical" href="https://papers.globalinfectiousdisease.com/research/articles/example/">'],
      main: '<main>Paper content</main>',
    } } };
    const recordHash = sha(JSON.stringify(record)), recordKey = `paper-template-v2/records/${recordHash}.json`;
    await bucket.put(recordKey, gzipSync(JSON.stringify(record)), { httpMetadata: { contentEncoding: 'gzip' } });
    async function version(name) {
      const prefix = '<html><head>', middle = `<link rel="stylesheet" href="/_astro/${name}.css"></head><body>`;
      const suffix = `<footer>${name}</footer></body></html>`;
      const template = { schema: 1, chunks: [prefix, { field: 'head', index: 0 }, middle, { field: 'main' }, suffix] };
      const templateHash = sha(JSON.stringify(template));
      await bucket.put(`paper-template-v2/templates/${templateHash}.json`, gzipSync(JSON.stringify(template)),
        { httpMetadata: { contentEncoding: 'gzip' } });
      const html = prefix + record.locales.en.head[0] + middle + record.locales.en.main + suffix;
      const entry = { logical_key: logical, key: recordKey, content_type: 'text/html; charset=utf-8', sha256: sha(html), bytes: Buffer.byteLength(html),
        paper_template: { slug: 'example', locale: 'en', template_sha256: templateHash, record_sha256: recordHash } };
      const shard = JSON.stringify({ schema_version: 1, items: [entry] });
      const shardKey = `releases/sha256/catalogue-shards/${sha(shard)}.json`;
      await bucket.put(shardKey, shard);
      const generation = sha(name);
      await bucket.put(`releases/sha256/catalogues/${generation}.json`, JSON.stringify({ schema_version: 2, generation_sha256: generation,
        shards: { [sha(logical).slice(0, 2)]: { key: shardKey } } }));
      return { generation, originalHash: sha(html), activate: () => bucket.put('site-data/research-publication/current.json',
        JSON.stringify({ schema_version: 1, release_id: name, papers: { papers_generation_sha256: generation }, files: {} })) };
    }
    const first = await version('first'); await first.activate();
    const response = await runtime.dispatchFetch(`${origin}/${logical}`);
    assert.equal(response.status, 503);
    assert.equal(response.headers.get('x-gids-paper-mode'), null);
    const next = await version('next'); await next.activate();
    const changed = await runtime.dispatchFetch(`${origin}/${logical}`);
    assert.equal(changed.status, 503);
    assert.equal(changed.headers.get('x-gids-research-release'), 'next');
    const retained = await runtime.dispatchFetch(`${origin}/research-publication/${first.generation}/${logical}`);
    assert.equal(retained.status, 410);
    assert.equal(retained.headers.get('location'), null);
    const papersHost = await runtime.dispatchFetch(`https://papers.globalinfectiousdisease.com/${logical}`);
    assert.equal(papersHost.status, 503);
    assert.equal(await bucket.get('paper-template-v2/current.json'), null);
  } finally { await runtime.dispose(); }
});

test('real template v2 records fail closed instead of assembling HTML', {
  skip: !process.env.GIDS_PAPERS_CANDIDATE || !process.env.GIDS_PAPERS_SOURCE,
}, async () => {
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modules: true, scriptPath: resolve('.wrangler/test-bundle/index.js'), compatibilityDate: '2026-11-07',
    compatibilityFlags: ['nodejs_compat'], r2Buckets: ['DATA'],
  }));
  const sha = value => createHash('sha256').update(value).digest('hex');
  try {
    const root = process.env.GIDS_PAPERS_CANDIDATE, source = process.env.GIDS_PAPERS_SOURCE;
    const pointer = JSON.parse(readFileSync(join(root, 'current.json'), 'utf8'));
    const generation = JSON.parse(readFileSync(join(root, 'generations', `${pointer.generation}.json`), 'utf8'));
    const recordFile = readdirSync(join(root, 'records')).sort()[0], recordHash = recordFile.replace('.json', '');
    const recordBytes = readFileSync(join(root, 'records', recordFile)), record = JSON.parse(recordBytes);
    const bucket = await runtime.getR2Bucket('DATA'), recordKey = `paper-template-v2/records/${recordFile}`;
    await bucket.put(recordKey, gzipSync(recordBytes), { httpMetadata: { contentEncoding: 'gzip' } });
    const groups = {}, expected = {};
    for (const locale of ['en', 'zh', 'fr']) {
      const templateHash = generation.locales[locale].template;
      await bucket.put(`paper-template-v2/templates/${templateHash}.json`, gzipSync(readFileSync(join(root, 'templates', `${templateHash}.json`))),
        { httpMetadata: { contentEncoding: 'gzip' } });
      const logical = `${locale === 'en' ? '' : locale + '/'}research/articles/${record.slug}/`;
      const original = readFileSync(join(source, logical, 'index.html'));
      const entry = { logical_key: logical, key: recordKey, content_type: 'text/html; charset=utf-8', sha256: sha(original), bytes: original.length,
        paper_template: { locale, slug: record.slug, template_sha256: templateHash, record_sha256: recordHash } };
      (groups[sha(logical).slice(0, 2)] ??= []).push(entry);
      expected[logical] = entry.sha256;
    }
    const descriptors = {};
    for (const [group, items] of Object.entries(groups)) {
      const body = JSON.stringify({ schema_version: 1, items });
      const key = `releases/sha256/catalogue-shards/${sha(body)}.json`;
      await bucket.put(key, body); descriptors[group] = { key };
    }
    const catalogueHash = sha(JSON.stringify(descriptors));
    await bucket.put(`releases/sha256/catalogues/${catalogueHash}.json`, JSON.stringify({ schema_version: 2, generation_sha256: catalogueHash, shards: descriptors }));
    await bucket.put('site-data/research-publication/current.json', JSON.stringify({ schema_version: 1, release_id: 'real-template-candidate',
      papers: { papers_generation_sha256: catalogueHash }, files: {} }));
    for (const [logical, digest] of Object.entries(expected)) {
      const response = await runtime.dispatchFetch(`https://papers.globalinfectiousdisease.com/${logical}`);
      assert.equal(response.status, 503);
      assert.equal(response.headers.get('x-gids-paper-mode'), null);
      assert.notEqual(response.headers.get('x-gids-paper-sha256'), digest);
    }
  } finally { await runtime.dispose(); }
});
