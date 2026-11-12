import test from 'node:test';
import assert from 'node:assert/strict';
import { articleHref, PAPERS_ORIGIN, researchVersionedHref, siteHref } from './papers.ts';

for (const hostname of ['globalinfectiousdisease.com', 'papers.globalinfectiousdisease.com']) {
  test(`stable navigation and pinned data from ${hostname}`, t => {
    const generation = 'b'.repeat(64);
    const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
    const oldDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
    Object.defineProperty(globalThis, 'location', { configurable: true, value: { pathname: '/research/', hostname } });
    Object.defineProperty(globalThis, 'document', { configurable: true, value: { querySelector: () => ({ content: generation }) } });
    t.after(() => {
      if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
      if (oldDocument) Object.defineProperty(globalThis, 'document', oldDocument); else Reflect.deleteProperty(globalThis, 'document');
    });
    const base = hostname.startsWith('papers.') ? 'https://globalinfectiousdisease.com' : '';
    for (const path of ['/research/catalogue/manifest.json', '/research/catalogue/studies/a.json',
      '/site-data/research/diseases/d1.json', '/site-data/research-graph.json', '/research/search-index.json?q=test']) {
      assert.equal(researchVersionedHref(path), `${base}/research-publication/${generation}${path}`);
      assert.equal(siteHref(path), `${base}/research-publication/${generation}${path}`);
    }
    for (const prefix of ['', '/zh', '/fr']) {
      for (const section of ['articles', 'diseases']) {
        const path = `${prefix}/research/${section}/a/?source=list#summary`;
        assert.equal(siteHref(path), PAPERS_ORIGIN + path);
        assert.equal(researchVersionedHref(path), path);
      }
      assert.equal(siteHref(`${prefix}/research/`), `${base}${prefix}/research/`);
    }
    assert.equal(articleHref('paper-a'), `${PAPERS_ORIGIN}/research/articles/paper-a/`);
    assert.equal(siteHref('/countries/us/'), '/countries/us/');
    assert.equal(siteHref('https://example.org/research/a'), 'https://example.org/research/a');
  });
}

test('a removed snapshot URL cannot supply the browser generation', t => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { pathname: `/research-publication/${'a'.repeat(64)}/research/` } });
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'location', previous); else Reflect.deleteProperty(globalThis, 'location');
  });
  assert.equal(researchVersionedHref('/research/catalogue/manifest.json'), '/research/catalogue/manifest.json');
});
