import type { APIRoute } from 'astro';
import { researchStudyCollections } from '../../../../lib/research-catalogue-shards';
import { independentResearchPages } from '../../../../lib/page-build-plan';

export const prerender = true;
export function getStaticPaths() {
  if (independentResearchPages()) return [];
  return researchStudyCollections().flatMap(collection => collection.chunks.map(chunk => ({ params: { collection: chunk.id }, props: { study: collection.study, articles: chunk.articles } })));
}
export const GET: APIRoute = ({ props }) => new Response(JSON.stringify({
  schema_version: 2,
  study: props.study,
  articles: props.articles,
}), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'public, max-age=31536000, immutable' } });
