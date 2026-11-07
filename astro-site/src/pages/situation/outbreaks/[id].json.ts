import type { APIRoute } from 'astro';
import { shouldBuildPage } from '../../../lib/page-build-plan';
import { readOutbreak, readSituationMap } from '../../../utils/international';

// The map island fetches one outbreak timeline at a time from here. The page
// ships markers only; quotes and summaries stay in this response.
export function getStaticPaths() {
  if (process.env.GIDS_PAGES_DATA_WORKER === '1') return [];
  return (readSituationMap()?.outbreaks ?? []).filter(outbreak => shouldBuildPage(`situation-outbreak:${outbreak.outbreak_id}`)).map(outbreak => ({ params: { id: outbreak.outbreak_id } }));
}

export const GET: APIRoute = ({ params }) => {
  const file = readOutbreak(String(params.id ?? ''));
  if (!file) return new Response('Not found', { status: 404 });
  return new Response(JSON.stringify(file), { headers: { 'Content-Type': 'application/json; charset=utf-8' } });
};
