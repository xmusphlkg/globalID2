import type { APIRoute } from 'astro';
import { PAPERS_SITEMAP_GROUP, renderSitemapXml } from '../../lib/sitemap-entries';
import { siteSitemapGroups } from '../../lib/site-sitemap';
import { PAPERS_ORIGIN } from '../../lib/papers';

const MAX_URLS = 50_000;

export const GET: APIRoute = () => {
  const entries = siteSitemapGroups()[PAPERS_SITEMAP_GROUP];
  if (entries.length > MAX_URLS) {
    throw new Error(`Papers sitemap has ${entries.length} URLs; split it into a sitemap index (limit ${MAX_URLS}).`);
  }
  return new Response(renderSitemapXml(entries, PAPERS_ORIGIN), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
