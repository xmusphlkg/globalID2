import type { APIRoute } from 'astro';
import { readResearchIndex } from '../../lib/generated-site-data';
import { researchSearchEntries } from '../../lib/research-search-entries';

export const GET: APIRoute = () => new Response(JSON.stringify({ schema_version: '1.0', entries: researchSearchEntries(readResearchIndex()) }),
  { headers: { 'Content-Type': 'application/json; charset=utf-8' } });
