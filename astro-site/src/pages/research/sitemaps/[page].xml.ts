import type { APIRoute } from 'astro';
import { siteSitemapGroups } from '../../../lib/site-sitemap';
import { renderSitemapXml } from '../../../lib/sitemap-entries';
import { researchSitemapPage, researchSitemapPageCount } from '../../../lib/research-sitemap';
import { independentResearchPages } from '../../../lib/page-build-plan';

export function getStaticPaths() {
  if (independentResearchPages()) return [];
  const groups = siteSitemapGroups();
  const count = researchSitemapPageCount(groups.research.length, groups.papers.length);
  return Array.from({ length: count }, (_, index) => ({ params: { page: String(index + 1) } }));
}

export const GET: APIRoute = ({ params, site }) => {
  const groups = siteSitemapGroups();
  const entries = researchSitemapPage(groups.research, groups.papers, Number(params.page));
  return new Response(renderSitemapXml(entries, site?.toString() ?? 'https://globalinfectiousdisease.com'),
    { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
