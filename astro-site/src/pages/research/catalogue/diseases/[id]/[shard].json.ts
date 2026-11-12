import type { APIRoute } from 'astro';
import { diseaseLiteratureCatalogue, diseaseLiteratureBundle } from '../../../../../lib/research-literature-server';
import { independentResearchPages } from '../../../../../lib/page-build-plan';

export function getStaticPaths() {
  if (independentResearchPages()) return [];
  return [...diseaseLiteratureCatalogue().byDisease.keys()].flatMap(id => diseaseLiteratureBundle(id).shards.map(shard => ({ params: { id, shard: shard.id } })));
}
export const GET: APIRoute = ({ params }) => {
  const shard = diseaseLiteratureBundle(String(params.id)).shards.find(row => row.id === params.shard);
  if (!shard) return new Response('Not found', { status: 404 });
  return new Response(JSON.stringify({ schema_version: 2, disease_id: String(params.id), shard: shard.id, articles: shard.articles }), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=31536000, immutable' } });
};
