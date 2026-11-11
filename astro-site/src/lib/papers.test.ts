import assert from 'node:assert/strict';
import test from 'node:test';
import { absoluteSiteUrl, isPapersPath, PAPERS_ORIGIN, siteHref } from './papers.ts';

test('disease literature and study collections belong to Papers', () => {
  for (const path of ['/research/diseases/influenza/', '/zh/research/diseases/dengue/', '/fr/research/diseases/measles/', '/research/catalogue/diseases/d038.json', '/research/catalogue/diseases/d038/0-0123456789abcdef.json', '/research/catalogue/studies/mathematical-modelling-0123456789abcdef.json']) {
    assert.equal(isPapersPath(path), true, path);
    assert.equal(siteHref(path), PAPERS_ORIGIN + path);
    assert.equal(absoluteSiteUrl(path, 'https://globalinfectiousdisease.com').origin, PAPERS_ORIGIN);
  }
  for (const path of ['/diseases/influenza/', '/research/', '/research/catalogue/entities.json', '/research/catalogue/manifest.json']) assert.equal(isPapersPath(path), false, path);
});
