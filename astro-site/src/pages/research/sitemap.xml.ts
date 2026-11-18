import type { APIRoute } from 'astro';
import { siteSitemapGroups } from '../../lib/site-sitemap';
import { renderResearchSitemapIndex, researchSitemapPages } from '../../lib/research-sitemap';

export const GET: APIRoute = ({ site }) => {
  const groups = siteSitemapGroups();
  const origin = site?.toString() ?? 'https://globalinfectiousdisease.com';
  const count = researchSitemapPages(groups.research, groups.papers, origin).length;
  return new Response(renderResearchSitemapIndex(origin, count),
    { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
