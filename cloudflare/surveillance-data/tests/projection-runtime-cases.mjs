import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
const sha = value => createHash('sha256').update(value).digest('hex');

test('real Worker serves v3 canonical HTML, preserves SEO and pins retained assets to its research generation', async () => {
  const runtime = new Miniflare(convertV4MiniflareOptions({ modules: true,
    scriptPath: resolve('.wrangler/test-bundle/index.js'), compatibilityDate: '2026-11-07',
    compatibilityFlags: ['nodejs_compat'], r2Buckets: ['DATA'], bindings: { PAPERS_PROJECTION_V3: '1' },
  }));
  try {
    const bucket = await runtime.getR2Bucket('DATA');
    const put = async (group, object) => { const raw = JSON.stringify(object), digest = sha(raw); await bucket.put(`paper-template-v3/${group}/${digest}.json`, raw); return digest; };
    const record = await put('records', { schema: 3, slug: 'runtime-paper', article_id: 'runtime-1', content: {
      title: 'Runtime projected evidence', editorial_status: 'published', summary: { en: { main_findings: 'Verified findings' } },
    } });
    const css = '.evidence-page{color:navy}', cssSha = sha(css), cssKey = `releases/sha256/objects/${cssSha}/${'a'.repeat(16)}`;
    await bucket.put(cssKey, css, { httpMetadata: { contentType: 'text/css' } });
    const assets = await put('assets', { schema: 1, items: [{ path: '_astro/evidence.css', key: cssKey, sha256: cssSha, bytes: css.length }] });
    const template = await put('templates', { schema: 3, content_schema: 3, renderer: 'gids-evidence-v1', locale: 'en', scope_attributes: [],
      shell: { schema: 1, chunks: ['<html><head>', { field: 'head', index: 0 }, '<link rel="stylesheet" href="/_astro/evidence.css"></head><body>', { field: 'main' }, '</body></html>'] } });
    const logical = 'research/articles/runtime-paper/';
    const entry = { logical_key: logical, key: `paper-template-v3/records/${record}.json`, integrity: 'independent-v3', content_type: 'text/html',
      paper_template: { schema: 3, locale: 'en', slug: 'runtime-paper', record_sha256: record, template_sha256: template, assets_sha256: assets } };
    const assetEntry = { logical_key: '_astro/evidence.css', key: cssKey, content_type: 'text/css' };
    const shards = {};
    for (const value of [entry, assetEntry]) {
      const body = JSON.stringify({ schema_version: 1, items: [value] }), key = `releases/sha256/catalogue-shards/${sha(body)}.json`;
      await bucket.put(key, body); shards[sha(value.logical_key).slice(0, 2)] = { key };
    }
    const generation = sha('runtime-projection-v3');
    await bucket.put(`releases/sha256/catalogues/${generation}.json`, JSON.stringify({ schema_version: 2, generation_sha256: generation, shards }));
    await bucket.put('site-data/research-publication/current.json', JSON.stringify({ schema_version: 1, release_id: 'runtime-v3', papers: { papers_generation_sha256: generation } }));
    const response = await runtime.dispatchFetch(`https://papers.globalinfectiousdisease.com/${logical}`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('X-GIDS-Paper-Content-SHA256'), record);
    assert.equal(response.headers.get('X-GIDS-Research-Release'), 'runtime-v3');
    const html = await response.text();
    assert.match(html, /Verified findings/);
    assert.match(html, /rel="canonical" href="https:\/\/papers.globalinfectiousdisease.com\/research\/articles\/runtime-paper\//);
    assert.ok(html.includes(`/research-publication/${generation}/_astro/evidence.css`));
    assert.equal(await (await runtime.dispatchFetch(`https://globalinfectiousdisease.com/research-publication/${generation}/_astro/evidence.css`)).text(), css);
    assert.equal((await runtime.dispatchFetch(`https://papers.globalinfectiousdisease.com/research-publication/${generation}/${logical}`)).status, 410);
  } finally { await runtime.dispose(); }
});
