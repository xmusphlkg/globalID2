import type { APIRoute } from 'astro';
import { downloadCatalogue } from '../../../../lib/downloads-data';

// Download metadata follows the existing archive/template release, independently
// of live surveillance. Each page fetches only its own small catalogue entry.
export function getStaticPaths() {
  const catalogue = downloadCatalogue();
  return (['countries', 'diseases'] as const).flatMap(kind => (catalogue?.[kind] ?? []).map(entry => ({
    params: { kind, id: String(kind === 'countries' ? entry.code : entry.disease_id ?? entry.id).toLowerCase() },
    props: { entry },
  })));
}
export const GET: APIRoute = ({ props }) => {
  const { entry } = props;
  const { generated_at, record_count, source_series_count, source_observation_count, includes_series_provenance, date_range, parts } = entry;
  return new Response(JSON.stringify({ schema_version: 1, generated_at, record_count, source_series_count, source_observation_count, includes_series_provenance, date_range, parts }), { headers: { 'content-type': 'application/json; charset=utf-8' } });
};
