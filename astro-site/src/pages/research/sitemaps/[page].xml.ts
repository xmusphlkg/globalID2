import type { APIRoute } from 'astro';
import { siteSitemapGroups } from '../../../lib/site-sitemap';
import { renderSitemapXml } from '../../../lib/sitemap-entries';
import { researchSitemapPages } from '../../../lib/research-sitemap';
import { independentResearchPages } from '../../../lib/page-build-plan';

export function getStaticPaths() {
  if (independentResearchPages()) return [];
  const groups = siteSitemapGroups();
  const origin = process.env.GLOBALID_SITE_URL || process.env.SITE_URL || 'https://globalinfectiousdisease.com';
  const count = researchSitemapPages(groups.research, groups.papers, origin).length;
  return Array.from({ length: count }, (_, index) => ({ params: { page: String(index + 1) } }));
}

export const GET: APIRoute = ({ params, site }) => {
  const groups = siteSitemapGroups();
  const origin = site?.toString() ?? 'https://globalinfectiousdisease.com';
  const entries = researchSitemapPages(groups.research, groups.papers, origin)[Number(params.page) - 1];
  return new Response(renderSitemapXml(entries, origin),
    { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
