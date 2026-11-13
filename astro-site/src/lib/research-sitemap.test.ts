import test from 'node:test';
import assert from 'node:assert/strict';
import { renderResearchSitemapIndex, researchSitemapPage, researchSitemapPageCount } from './research-sitemap.ts';
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
  const research = [0, 1], papers = Array.from({ length: 45000 }, (_, index) => index + 2);
  assert.equal(researchSitemapPageCount(research.length, papers.length), 2);
  assert.equal(researchSitemapPage(research, papers, 1).length, 45000);
  assert.deepEqual(researchSitemapPage(research, papers, 2), [45000, 45001]);
  assert.match(renderResearchSitemapIndex('https://site.example', 2), /research\/sitemaps\/2\.xml/);
  assert.match(renderSitemapIndexXml(['static'], 'https://site.example', { researchPublication: true }), /research\/sitemap\.xml/);
});
