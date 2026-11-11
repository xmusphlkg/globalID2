import type { APIRoute } from 'astro';
import { renderSitemapIndexXml, SITEMAP_GROUPS } from '../lib/sitemap-entries';

const fallbackSite = 'https://globalinfectiousdisease.com';

export const GET: APIRoute = ({ site }) => new Response(
  renderSitemapIndexXml(import.meta.env.PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN === '1'
    ? SITEMAP_GROUPS.filter(group => group !== 'research') : [...SITEMAP_GROUPS], site?.toString() ?? fallbackSite,
    { researchPublication: import.meta.env.PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN === '1' }),
  { headers: { 'Content-Type': 'application/xml; charset=utf-8' } },
);
