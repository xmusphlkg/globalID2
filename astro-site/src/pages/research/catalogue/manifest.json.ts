import { researchCatalogueChunks, researchCatalogueChunkNames, researchCatalogueGeneratedAt, researchCatalogueArticles } from '../../../lib/research-catalogue-shards';

export function GET() {
  const slugifyTopic = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const facets = (articles: any[]) => ({
    disease: [...new Set(articles.flatMap(article => (article.diseases ?? []).map((item: any) => String(item.disease_id ?? '').toLowerCase())).filter(Boolean))],
    country: [...new Set(articles.flatMap(article => (article.countries ?? []).map((item: any) => String(item.code ?? '').toLowerCase())).filter(Boolean))],
    topic: [...new Set(articles.flatMap(article => (article.topics ?? []).map((item: any) => slugifyTopic(String(item.name ?? '')))).filter(Boolean))],
    week: [...new Set(articles.map(article => String(article.publication_week ?? '').toLowerCase()).filter(Boolean))],
  });
  return new Response(JSON.stringify({
    schema_version: 2,
    generated_at: researchCatalogueGeneratedAt,
    total: researchCatalogueArticles.length,
    chunks: researchCatalogueChunks.map((articles, index) => ({ path: `/research/catalogue/${researchCatalogueChunkNames[index]}.json`, count: articles.length, facets: facets(articles) })),
  }), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });
}
