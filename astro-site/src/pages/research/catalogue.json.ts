import researchData from '../../data/research/index.json';
import { compactResearchArticle } from '../../lib/research-catalogue';

export function GET() {
  return new Response(JSON.stringify({
    schema_version: 1,
    generated_at: (researchData as any).last_updated ?? null,
    articles: ((researchData as any).articles ?? [])
      .filter((article: any) => article.peer_review_status === 'peer_reviewed' || article.source_kind === 'historical_seed')
      .map(compactResearchArticle),
  }), {
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=300',
    },
  });
}
