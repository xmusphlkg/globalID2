import type { APIRoute } from 'astro';
import { siteSitemapGroups } from '../../lib/site-sitemap';
import { renderResearchSitemapIndex, researchSitemapPageCount } from '../../lib/research-sitemap';

export const GET: APIRoute = ({ site }) => {
  const groups = siteSitemapGroups();
  const count = researchSitemapPageCount(groups.research.length, groups.papers.length);
  const origin = site?.toString() ?? 'https://globalinfectiousdisease.com';
  return new Response(renderResearchSitemapIndex(origin, count),
    { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
