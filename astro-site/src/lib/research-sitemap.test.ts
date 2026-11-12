import test from 'node:test';
import assert from 'node:assert/strict';
import { renderResearchSitemapIndex, researchSitemapPage, researchSitemapPageCount } from './research-sitemap.ts';
import { renderSitemapIndexXml } from './sitemap-entries.ts';

test('research sitemaps paginate without dropping records at the boundary', () => {
  const research = [0, 1], papers = Array.from({ length: 45000 }, (_, index) => index + 2);
  assert.equal(researchSitemapPageCount(research.length, papers.length), 2);
  assert.equal(researchSitemapPage(research, papers, 1).length, 45000);
  assert.deepEqual(researchSitemapPage(research, papers, 2), [45000, 45001]);
  assert.match(renderResearchSitemapIndex('https://site.example', 2), /research\/sitemaps\/2\.xml/);
  assert.match(renderSitemapIndexXml(['static'], 'https://site.example', { researchPublication: true }), /research\/sitemap\.xml/);
});
