import { buildResearchArticleSeo } from './research-article-seo.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildResearchItemList, researchCoverage } from './research-seo.ts';

test('article metadata preserves identifiers and localized evidence within snippet limits', () => {
  for (const locale of ['en', 'zh', 'fr'] as const) {
    const seo = buildResearchArticleSeo({ title: 'Research '.repeat(50), slug: 'article-a', doi: '10.1234/paper', summary: { [locale]: { main_findings: 'Findings '.repeat(50) } } }, locale);
    assert.ok(seo.title.length <= 70);
    assert.ok(seo.description.length <= 160);
    assert.ok(seo.title.includes('paper'));
    assert.match(seo.abstract!, /Findings/);
    assert.deepEqual(seo.identifier, { '@type': 'PropertyValue', propertyID: 'DOI', value: '10.1234/paper', url: 'https://doi.org/10.1234/paper' });
    assert.ok(!seo.description.includes('undefined'));
  }
});

test('collection schema describes initial HTML and never invents detail URLs for source-only records', () => {
  const records = [
    { slug: 'summary', title: '<i>Evidence</i>' },
    { slug: 'not-a-detail-page', title: 'Source paper', detail_available: false, doi: '10.1234/source' },
    { title: 'PubMed record', detail_available: false, pmid: '123' },
    { slug: 'load-more', title: 'Later record' },
  ];
  const schema = buildResearchItemList(records, 'https://globalinfectiousdisease.com', 'zh', 3);
  assert.equal(schema.numberOfItems, 4);
  assert.equal(schema.itemListElement.length, 3);
  assert.equal(schema.itemListElement[0].name, 'Evidence');
  assert.match(schema.itemListElement[0].url, /\/zh\/research\/articles\/summary\/$/);
  assert.equal(schema.itemListElement[1].url, 'https://doi.org/10.1234/source');
  assert.equal(schema.itemListElement[2].url, 'https://pubmed.ncbi.nlm.nih.gov/123/');
  assert.ok(!JSON.stringify(schema).includes('load-more'));
  assert.ok(!JSON.stringify(schema).includes('not-a-detail-page'));
});

test('coverage counts a topic once per paper and excludes undated records from the year range', () => {
  const coverage = researchCoverage([
    { published_at: '2026-11-01', topics: [{ name: 'Surveillance' }, { name: 'Surveillance' }] },
    { published_at: '2008-02-01', topics: [{ name: 'Surveillance' }, { name: 'Vaccination' }] },
    { published_at: 'unknown', topics: [] },
  ]);
  assert.deepEqual(coverage, { startYear: '2008', endYear: '2026', topics: [['Surveillance', 2], ['Vaccination', 1]] });
  assert.deepEqual(researchCoverage([]), { startYear: undefined, endYear: undefined, topics: [] });
});
