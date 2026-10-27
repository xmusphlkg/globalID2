import { createHash } from 'node:crypto';
import researchData from '../data/research/index.json';
import { compactResearchArticle } from './research-catalogue';

export const researchCatalogueChunkSize = 100;
export const researchCatalogueArticles = ((researchData as any).articles ?? [])
  .filter((article: any) => article.peer_review_status === 'peer_reviewed' || article.source_kind === 'historical_seed')
  .map(compactResearchArticle);
export const researchCatalogueChunks = Array.from(
  { length: Math.ceil(researchCatalogueArticles.length / researchCatalogueChunkSize) },
  (_, index) => researchCatalogueArticles.slice(index * researchCatalogueChunkSize, (index + 1) * researchCatalogueChunkSize),
);
export const researchCatalogueGeneratedAt = (researchData as any).last_updated ?? null;
export const researchCatalogueChunkNames = researchCatalogueChunks.map((articles, index) => {
  const hash = createHash('sha256').update(JSON.stringify(articles)).digest('hex').slice(0, 16);
  return `${index}-${hash}`;
});
