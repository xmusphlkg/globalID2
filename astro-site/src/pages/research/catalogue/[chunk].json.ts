import type { APIRoute } from 'astro';
import { researchCatalogueChunks, researchCatalogueChunkNames } from '../../../lib/research-catalogue-shards';

export const prerender = true;

export function getStaticPaths() {
  return researchCatalogueChunks.map((_, index) => ({ params: { chunk: researchCatalogueChunkNames[index] }, props: { index } }));
}

export const GET: APIRoute = ({ props }) => new Response(JSON.stringify({
  schema_version: 2,
  articles: researchCatalogueChunks[Number(props.index)],
}), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=31536000, immutable' } });
