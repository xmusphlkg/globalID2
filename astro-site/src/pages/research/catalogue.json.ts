import researchData from '../../data/research/index.json';

const compactArticle = (article: any) => ({
  article_id: article.article_id,
  slug: article.slug,
  title: article.title,
  doi: article.doi,
  journal: article.journal,
  study_type: article.study_type,
  published_at: article.published_at,
  open_access_status: article.open_access_status,
  open_access_url: article.open_access_url,
  peer_review_status: article.peer_review_status,
  discovery_score: article.discovery_score,
  diseases: article.diseases ?? [],
  countries: article.countries ?? [],
  topics: article.topics ?? [],
  why_it_matters_en: article.why_it_matters_en,
  why_it_matters_zh: article.why_it_matters_zh,
});

export function GET() {
  return new Response(JSON.stringify({
    schema_version: 1,
    generated_at: (researchData as any).last_updated ?? null,
    articles: ((researchData as any).articles ?? []).map(compactArticle),
  }), {
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'public, max-age=300',
    },
  });
}
