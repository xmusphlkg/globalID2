import type { APIRoute } from 'astro';
import { researchCatalogue } from '../../../lib/research-catalogue-shards';
import { independentResearchPages } from '../../../lib/page-build-plan';

export const prerender = true;

export function getStaticPaths() {
  if (independentResearchPages()) return [];
  const { researchCatalogueChunks, researchCatalogueChunkNames } = researchCatalogue();
  return researchCatalogueChunks.map((_, index) => ({ params: { chunk: researchCatalogueChunkNames[index] }, props: { index } }));
}

export const GET: APIRoute = ({ props }) => new Response(JSON.stringify({
  schema_version: 2,
  articles: researchCatalogue().researchCatalogueChunks[Number(props.index)],
}), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=31536000, immutable' } });
