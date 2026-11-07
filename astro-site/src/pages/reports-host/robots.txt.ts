import type { APIRoute } from 'astro';
import { REPORTS_ORIGIN } from '../../lib/papers';

export const GET: APIRoute = () => new Response(
  `User-agent: *\nAllow: /\nSitemap: ${REPORTS_ORIGIN}/sitemap.xml\n`,
  { headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
);
