import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadResearchCatalogue } from '../src/lib/research-catalogue-client.ts';

test('loads only requested catalogue chunks and narrows collection requests', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const fixtures = {
    '/research/catalogue/manifest.json': { chunks: [
      { path: '/research/catalogue/first.json', count: 2, facets: { disease: ['d001'], country: [], topic: [], week: [] } },
      { path: '/research/catalogue/second.json', count: 1, facets: { disease: ['d002'], country: [], topic: [], week: [] } },
    ] },
    '/research/catalogue/first.json': { articles: [{ slug: 'one' }, { slug: 'two' }] },
    '/research/catalogue/second.json': { articles: [{ slug: 'three' }] },
  };
  globalThis.fetch = async path => {
    requests.push(path);
    return { ok: true, json: async () => fixtures[path] };
  };
  try {
    assert.deepEqual((await loadResearchCatalogue({ limit: 1 })).map(item => item.slug), ['one', 'two']);
    assert.deepEqual(requests, ['/research/catalogue/manifest.json', '/research/catalogue/first.json']);
    assert.deepEqual((await loadResearchCatalogue({ scope: 'disease', value: 'd002' })).map(item => item.slug), ['three']);
    assert.equal(requests.filter(path => path === '/research/catalogue/first.json').length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
