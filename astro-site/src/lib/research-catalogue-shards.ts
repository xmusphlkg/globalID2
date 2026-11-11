import { readResearchIndex } from './generated-site-data.ts';
import { memoizeForBuild } from './build-memo.ts';
import { createHash } from 'node:crypto';
import { compactResearchArticle } from './research-catalogue.ts';

export const researchCatalogueChunkSize = 100;
export const researchCatalogue = memoizeForBuild(() => {
const researchIndex = readResearchIndex() as any;
const researchCatalogueArticles = [...(researchIndex.articles ?? []), ...(researchIndex.bibliographic_articles ?? [])]
  .filter((article: any) => article.peer_review_status === 'peer_reviewed' || article.source_kind === 'historical_seed')
  .map(compactResearchArticle)
  .sort((a: any, b: any) => String(b.published_at ?? '').localeCompare(String(a.published_at ?? '')));
const researchCatalogueChunks = Array.from(
  { length: Math.ceil(researchCatalogueArticles.length / researchCatalogueChunkSize) },
  (_, index) => researchCatalogueArticles.slice(index * researchCatalogueChunkSize, (index + 1) * researchCatalogueChunkSize),
);
const researchCatalogueGeneratedAt = researchIndex.last_updated ?? null;
const researchCatalogueReviewedPreprints = (researchIndex.preprints ?? [])
  .filter((article: any) => article.peer_review_status === 'preprint' && article.editorial_status === 'published')
  .map(compactResearchArticle);
const researchCatalogueChunkNames = researchCatalogueChunks.map((articles, index) => {
  const hash = createHash('sha256').update(JSON.stringify(articles)).digest('hex').slice(0, 16);
  return `${index}-${hash}`;
});

return { researchCatalogueArticles, researchCatalogueChunks, researchCatalogueGeneratedAt, researchCatalogueChunkNames, researchCatalogueReviewedPreprints };
});

/** Complete per-study projections avoid fetching mixed, date-ordered chunks. */
export function buildResearchStudyCollections(articles: any[]) {
  const groups = new Map<string, any[]>();
  for (const article of articles) {
    const study = String(article.study_type ?? '').trim().toLowerCase();
    if (!study) continue;
    const rows = groups.get(study) ?? [];
    rows.push(article); groups.set(study, rows);
  }
  return [...groups].map(([study, records]) => {
    const slug = study.replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'study';
    // Small collections use at most ten pages to avoid hundreds of tiny
    // historical-month requests. Crossing 1000 repartitions once; large study
    // groups isolate later insertions/backfills to their publication month.
    const buckets = new Map<string, any[]>();
    for (const article of records) {
      const date = String(article.published_at ?? '');
      const month = /^(18\d{2}|19\d{2}|20\d{2}|21\d{2})-(0[1-9]|1[0-2])(?:-|$)/.exec(date);
      const bucket = records.length <= 1000 ? 'all' : month ? `${month[1]}${month[2]}` : '000000';
      const rows = buckets.get(bucket) ?? [];
      rows.push(article); buckets.set(bucket, rows);
    }
    const chunks = [...buckets].sort(([a], [b]) => b.localeCompare(a)).flatMap(([bucket, rows]) => {
      const ordered = [...rows].sort((a, b) => String(b.published_at ?? '').localeCompare(String(a.published_at ?? '')) || String(a.article_id ?? a.doi ?? a.slug ?? '').localeCompare(String(b.article_id ?? b.doi ?? b.slug ?? '')));
      return Array.from({ length: Math.ceil(ordered.length / researchCatalogueChunkSize) }, (_, page) => {
        const articles = ordered.slice(page * researchCatalogueChunkSize, (page + 1) * researchCatalogueChunkSize);
        const hash = createHash('sha256').update(JSON.stringify(articles)).digest('hex').slice(0, 16);
        const id = `${slug}-${bucket}${page}-${hash}`;
        return { id, path: `/research/catalogue/studies/${id}.json`, count: articles.length, articles };
      });
    });
    return { study, count: records.length, chunks };
  });
}

export const researchStudyCollections = memoizeForBuild(() => buildResearchStudyCollections(researchCatalogue().researchCatalogueArticles));
