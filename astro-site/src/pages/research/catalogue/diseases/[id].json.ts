import type { APIRoute } from 'astro';
import { diseaseLiteratureCatalogue, diseaseLiteratureBundle } from '../../../../lib/research-literature-server';

export function getStaticPaths() {
  return [...diseaseLiteratureCatalogue().byDisease.keys()].map(id => ({ params: { id } }));
}
export const GET: APIRoute = ({ params }) => {
  return new Response(JSON.stringify(diseaseLiteratureBundle(String(params.id)).index), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-cache' } });
};
