import type { APIRoute } from 'astro';
import { readMonitoring, readSituationMap, readSources } from '../../utils/international';
import type { SituationMapDataFile } from '../../utils/situationTypes';

// The situation stage fetches markers and monitoring from here. Keeping them
// out of the HTML props is what keeps /zh/situation/ under the page gzip budget
// after the three-year history backfill.

export const GET: APIRoute = () => {
  if (process.env.GIDS_PAGES_DATA_WORKER === '1') return new Response('Independent Situation publication required', { status: 404 });
  const map = readSituationMap();
  if (!map) return new Response('Not found', { status: 404 });
  const sources = readSources();
  const payload: SituationMapDataFile = {
    schema_version: map.schema_version,
    generated_at: map.generated_at,
    as_of: map.as_of,
    points: map.points,
    monitoring: readMonitoring(),
    active_outbreaks: map.outbreaks.filter(outbreak => outbreak.status === 'active').length,
    sources_ok: sources.filter(source => source.status === 'ok').length,
    source_count: sources.length,
  };
  return new Response(JSON.stringify(payload), {
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });
};
