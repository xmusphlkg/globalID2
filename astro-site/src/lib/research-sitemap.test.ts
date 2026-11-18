import test from 'node:test';
import assert from 'node:assert/strict';
import { RESEARCH_SITEMAP_PAGE_SIZE, renderResearchSitemapIndex, researchSitemapPages } from './research-sitemap.ts';
import { renderSitemapIndexXml, renderSitemapXml } from './sitemap-entries.ts';
import { absoluteSiteUrl, articleHref } from './papers.ts';

test('research sitemap and language alternates match the public article canonical', () => {
  const path = '/research/articles/paper-a/';
  const xml = renderSitemapXml([{ path, alternates: [
    { locale: 'en', path }, { locale: 'zh-CN', path: '/zh/research/articles/paper-a/' },
    { locale: 'fr-FR', path: '/fr/research/articles/paper-a/' },
  ] }, { path: '/research/' }], 'https://globalinfectiousdisease.com');
  assert.ok(xml.includes(`<loc>${absoluteSiteUrl(path, 'https://globalinfectiousdisease.com')}</loc>`));
  for (const locale of ['en', 'zh', 'fr'] as const) assert.ok(xml.includes(`href="${articleHref('paper-a', locale)}"`));
  assert.ok(xml.includes('<loc>https://globalinfectiousdisease.com/research/</loc>'));
  assert.ok(!xml.includes('/research-publication/'));
});

test('research sitemaps paginate without dropping records at the boundary', () => {
  const entries = Array.from({ length: RESEARCH_SITEMAP_PAGE_SIZE + 2 }, (_, index) => ({ path: `/research/${index}/` }));
  const pages = researchSitemapPages(entries.slice(0, 2), entries.slice(2), 'https://site.example');
  assert.equal(pages.length, 2);
  assert.equal(pages[0].length, RESEARCH_SITEMAP_PAGE_SIZE);
  assert.deepEqual(pages[1], entries.slice(RESEARCH_SITEMAP_PAGE_SIZE));
  assert.deepEqual(pages.flat(), entries);
  assert.match(renderResearchSitemapIndex('https://site.example', 2), /research\/sitemaps\/2\.xml/);
  assert.match(renderSitemapIndexXml(['static'], 'https://site.example', { researchPublication: true }), /research\/sitemap\.xml/);
});

test('multilingual research sitemap partitions fit the Pages single-file budget', () => {
  // The previous 45,000-URL limit produced a 25.4 MiB asset even though the
  // sitemap protocol permits 50 MiB. Long article slugs and four hreflang
  // alternates must fit the stricter hosting limit too.
  const entries = Array.from({ length: 45000 }, (_, index) => {
    const path = `/research/articles/${'long-article-slug-'.repeat(12)}${index}/`;
    return { path, lastmod: '2026-11-18T00:00:00.000Z', alternates: [
      { locale: 'en' as const, path },
      { locale: 'zh-CN' as const, path: `/zh${path}` },
      { locale: 'fr-FR' as const, path: `/fr${path}` },
      { locale: 'x-default' as const, path },
    ] };
  });
  const pages = researchSitemapPages([], entries, 'https://globalinfectiousdisease.com');
  assert.deepEqual(pages.flat(), entries);
  for (const page of pages) {
    const xml = renderSitemapXml(page, 'https://globalinfectiousdisease.com');
    assert.ok(Buffer.byteLength(xml) < 25 * 1024 * 1024);
  }
});

test('research sitemap byte partitions preserve UTF-8 URLs across group boundaries', () => {
  const entries = Array.from({ length: 7 }, (_, i) => ({ path: `/research/${'é漢&'.repeat(12)}${i}/` }));
  const limit = Buffer.byteLength(renderSitemapXml([entries[0]], 'https://site.example')) * 2;
  const pages = researchSitemapPages(entries.slice(0, 3), entries.slice(3), 'https://site.example', limit);
  assert.ok(pages.length > 1);
  assert.deepEqual(pages.flat(), entries);
  for (const page of pages) assert.ok(Buffer.byteLength(renderSitemapXml(page, 'https://site.example')) <= limit);
  assert.throws(() => researchSitemapPages(entries, [], 'https://site.example', 32), /byte budget/);
});
