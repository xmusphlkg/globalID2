import test from 'node:test';
import assert from 'node:assert/strict';
import { researchVersionedHref, siteHref } from './papers.ts';

test('interactive research links remain pinned to the page generation', () => {
  const generation = 'a'.repeat(64);
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'location');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { pathname: `/research-publication/${generation}/research/` } });
  try {
    for (const path of ['/research/catalogue/manifest.json', '/research/catalogue/studies/a.json',
      '/site-data/research/diseases/d1.json', '/site-data/research-graph.json', '/zh/research/articles/a/']) {
      assert.equal(researchVersionedHref(path), `/research-publication/${generation}${path}`);
      assert.equal(siteHref(path), `/research-publication/${generation}${path}`);
    }
    assert.equal(siteHref('/countries/us/'), '/countries/us/');
    assert.equal(siteHref('https://example.org/research/a'), 'https://example.org/research/a');
  } finally {
    if (previous) Object.defineProperty(globalThis, 'location', previous);
    else Reflect.deleteProperty(globalThis, 'location');
  }
});

test('stable research page uses its embedded generation', () => {
  const generation = 'b'.repeat(64);
  const oldLocation = Object.getOwnPropertyDescriptor(globalThis, 'location');
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  Object.defineProperty(globalThis, 'location', { configurable: true, value: { pathname: '/research/', hostname: 'globalinfectiousdisease.com' } });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { querySelector: () => ({ content: generation }) } });
  try {
    assert.equal(siteHref('/research/articles/a/'), `/research-publication/${generation}/research/articles/a/`);
  } finally {
    if (oldLocation) Object.defineProperty(globalThis, 'location', oldLocation); else Reflect.deleteProperty(globalThis, 'location');
    if (oldDocument) Object.defineProperty(globalThis, 'document', oldDocument); else Reflect.deleteProperty(globalThis, 'document');
  }
});
