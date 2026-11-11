import { createHash } from 'node:crypto';
import { compactResearchArticle } from './research-catalogue.ts';
import { literatureArticle, literaturePageSize, literatureRecordId, literatureIndexRecord } from './research-literature.ts';
import { localizedRegionName } from '../utils/i18n.ts';
import { toSeoSlug } from './seo.ts';

/** Shared by rendering and incremental cache keys: include every public collection. */
export function projectDiseaseLiterature(articles: any[]) {
  const byDisease = new Map<string, any[]>();
  const diseaseFacets = new Map<string, any>();
  for (const article of articles) {
    const row = literatureArticle(article);
    // Freeze locale fallback labels into the server projection: Node and the
    // browser can ship different ICU region names, which would break hydration.
    row.countries = row.countries.map((country: any) => ({
      ...country,
      name_en: localizedRegionName('en', country.code, { en: country.name_en }),
      name_zh: localizedRegionName('zh', country.code, { en: country.name_en, zh: country.name_zh }),
      name_fr: localizedRegionName('fr', country.code, { en: country.name_en, fr: country.name_fr }),
    }));
    for (const disease of article.diseases ?? []) {
      const id = String(disease.disease_id ?? '').toLowerCase();
      if (id && disease.slug && !diseaseFacets.has(id)) diseaseFacets.set(id, { ...disease, slug: toSeoSlug(disease.slug) });
    }
    for (const id of new Set((article.diseases ?? []).map((x: any) => String(x.disease_id ?? '').toLowerCase()).filter(Boolean))) {
      const rows = byDisease.get(id as string) ?? [];
      rows.push(row); byDisease.set(id as string, rows);
    }
  }
  return { byDisease, diseaseFacets };
}

export function diseaseLiteratureFromIndex(index: any) {
  const reviewed = [...(index.articles ?? []), ...(index.bibliographic_articles ?? [])]
    .filter(article => article.peer_review_status === 'peer_reviewed' || article.source_kind === 'historical_seed')
    .map(compactResearchArticle)
    .sort((a, b) => String(b.published_at ?? '').localeCompare(String(a.published_at ?? '')));
  const preprints = (index.preprints ?? []).filter((article: any) => article.peer_review_status === 'preprint' && article.editorial_status === 'published').map(compactResearchArticle);
  return projectDiseaseLiterature([...reviewed, ...preprints]);
}

/** Monthly boundaries keep historical immutable pages stable when new work arrives. */
export function diseaseLiteratureShards(diseaseId: string, articles: any[]) {
  const months = new Map<string, any[]>();
  for (const article of articles) {
    const date = String(article.published_at ?? '');
    const match = /^(18\d{2}|19\d{2}|20\d{2}|21\d{2})-(0[1-9]|1[0-2])(?:-|$)/.exec(date);
    const month = match ? `${match[1]}${match[2]}` : '000000';
    const rows = months.get(month) ?? [];
    rows.push(article); months.set(month, rows);
  }
  return [...months].sort(([a], [b]) => b.localeCompare(a)).flatMap(([month, rows]) => {
    const ordered = [...rows].sort((a, b) => String(b.published_at ?? '').localeCompare(String(a.published_at ?? ''))
      || literatureRecordId(a).localeCompare(literatureRecordId(b)) || JSON.stringify(a).localeCompare(JSON.stringify(b)));
    return Array.from({ length: Math.ceil(ordered.length / literaturePageSize) }, (_, page) => {
      const records = ordered.slice(page * literaturePageSize, (page + 1) * literaturePageSize);
      const digest = createHash('sha256').update(JSON.stringify(records)).digest('hex');
      // Six fixed month digits followed by the numeric page avoid month/page collisions.
      const id = `${month}${page}-${digest.slice(0, 16)}`;
      return { id, url: `/research/catalogue/diseases/${diseaseId}/${id}.json`, count: records.length, articles: records };
    });
  });
}

export function diseaseLiteratureBundleData(diseaseId: string, articles: any[]) {
  const shards = diseaseLiteratureShards(diseaseId, articles);
  const records = shards.flatMap(shard => shard.articles.map(article => literatureIndexRecord(article, shard.id)));
  return { index: { schema_version: 2, disease_id: diseaseId, total: records.length, page_size: literaturePageSize,
    records, shards: shards.map(({ id, url, count }) => ({ id, url, count })) }, shards };
}
