import type { APIRoute } from 'astro';
import { renderSitemapXml, SITEMAP_GROUPS, type SitemapGroup } from '../../lib/sitemap-entries';
import { siteSitemapGroups } from '../../lib/site-sitemap';

const fallbackSite = 'https://globalinfectiousdisease.com';

export function getStaticPaths() {
  return SITEMAP_GROUPS.filter(name => name !== 'reports').map(name => ({ params: { name } }));
}

export const GET: APIRoute = ({ params, site }) => {
  const name = params.name as SitemapGroup;
  if (!SITEMAP_GROUPS.includes(name)) return new Response('Not found', { status: 404 });
  const entries = siteSitemapGroups()[name];
  return new Response(renderSitemapXml(entries, site?.toString() ?? fallbackSite), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
