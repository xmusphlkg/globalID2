import type { APIRoute } from 'astro';
import { PAPERS_ORIGIN } from '../../lib/papers';

// Emitted to dist/papers-host/ and moved to the root of the papers host by the
// release split step.
export const GET: APIRoute = () => new Response(
  [
    'User-agent: *',
    'Allow: /',
    '',
    `Sitemap: ${PAPERS_ORIGIN}/sitemap.xml`,
  ].join('\n'),
  { headers: { 'Content-Type': 'text/plain; charset=utf-8' } },
);
