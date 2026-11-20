import type { APIRoute } from 'astro';
import { downloadCatalogue } from '../../../../lib/downloads-data';

// Old bookmarks receive a download-center link, never a frozen archive URL.
export function getStaticPaths() {
  const catalogue = downloadCatalogue();
  return (['countries', 'diseases'] as const).flatMap(kind => (catalogue?.[kind] ?? []).map(entry => ({
    params: { kind, id: String(kind === 'countries' ? entry.code : entry.disease_id ?? entry.id).toLowerCase() },
    props: { kind, id: String(kind === 'countries' ? entry.code : entry.disease_id ?? entry.id).toUpperCase() },
  })));
}
export const GET: APIRoute = ({ props }) => {
  const query = new URLSearchParams({ [props.kind === 'countries' ? 'country' : 'disease']: props.id });
  return new Response(JSON.stringify({ schema_version: 2, download_center: `/downloads/?${query}` }), {
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
};
