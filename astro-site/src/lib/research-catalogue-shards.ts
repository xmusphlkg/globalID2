import { readResearchIndex } from './generated-site-data.ts';
import { memoizeForBuild } from './build-memo.ts';
import { createHash } from 'node:crypto';
import { compactResearchArticle } from './research-catalogue';

export const researchCatalogueChunkSize = 100;
export const researchCatalogue = memoizeForBuild(() => {
const researchCatalogueArticles = ((readResearchIndex() as any).articles ?? [])
  .filter((article: any) => article.peer_review_status === 'peer_reviewed' || article.source_kind === 'historical_seed')
  .map(compactResearchArticle);
const researchCatalogueChunks = Array.from(
  { length: Math.ceil(researchCatalogueArticles.length / researchCatalogueChunkSize) },
  (_, index) => researchCatalogueArticles.slice(index * researchCatalogueChunkSize, (index + 1) * researchCatalogueChunkSize),
);
const researchCatalogueGeneratedAt = (readResearchIndex() as any).last_updated ?? null;
const researchCatalogueChunkNames = researchCatalogueChunks.map((articles, index) => {
  const hash = createHash('sha256').update(JSON.stringify(articles)).digest('hex').slice(0, 16);
  return `${index}-${hash}`;
});

return { researchCatalogueArticles, researchCatalogueChunks, researchCatalogueGeneratedAt, researchCatalogueChunkNames };
});
