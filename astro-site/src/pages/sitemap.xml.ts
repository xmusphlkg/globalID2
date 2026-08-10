import type { APIRoute } from 'astro';
import { renderSitemapIndexXml, SITEMAP_GROUPS } from '../lib/sitemap-entries';

const fallbackSite = 'https://globalinfectiousdisease.com';

export const GET: APIRoute = ({ site }) => new Response(
  renderSitemapIndexXml([...SITEMAP_GROUPS], site?.toString() ?? fallbackSite),
  { headers: { 'Content-Type': 'application/xml; charset=utf-8' } },
);
