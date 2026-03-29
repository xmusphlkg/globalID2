import type { APIRoute } from 'astro';

const fallbackSite = 'https://globalinfectiousdisease.com';

export const GET: APIRoute = ({ site }) => {
  const siteUrl = new URL(site?.toString() ?? fallbackSite);
  const body = [
    'User-agent: *',
    'Allow: /',
    '',
    `Host: ${siteUrl.host}`,
    `Sitemap: ${new URL('/sitemap.xml', siteUrl).toString()}`,
  ].join('\n');

  return new Response(body, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
};
