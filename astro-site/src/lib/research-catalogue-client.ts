import { researchVersionedHref } from './papers.ts';
import { researchQueryScopes, type ResearchQueryEntity } from './research-query-scope.ts';

type CatalogueScope = 'disease' | 'country' | 'topic' | 'study' | 'week';
export type CatalogueFacets = Partial<Record<CatalogueScope, string>>;
type CatalogueChunk = { path: string; count: number; reviewed_count?: number; facets?: Partial<Record<CatalogueScope, string[]>> };
type CatalogueManifest = { chunks: CatalogueChunk[] };
export type CatalogueProgress = { mode: 'catalogue' | 'collection'; completed: number; total: number };
type CatalogueRequest = { limit?: number; scope?: CatalogueScope; value?: string; facets?: CatalogueFacets; includeBibliographic?: boolean; onProgress?: (progress: CatalogueProgress) => void };
export const researchCatalogueConcurrency = 4;

// Bound network work across overlapping filter requests, not just per caller.
let activeChunkRequests = 0;
const queuedChunkRequests: Array<() => void> = [];
function queueChunkRequest<T>(task: () => Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const run = () => {
      activeChunkRequests++;
      Promise.resolve().then(task).then(resolve, reject).finally(() => {
        activeChunkRequests--;
        queuedChunkRequests.shift()?.();
      });
    };
    if (activeChunkRequests < researchCatalogueConcurrency) run();
    else queuedChunkRequests.push(run);
  });
}

let manifestPromise: Promise<CatalogueManifest> | null = null;
const chunkPromises = new Map<string, Promise<any[]>>();

export async function loadResearchCatalogue({ limit, scope, value, facets, includeBibliographic = false, onProgress }: CatalogueRequest = {}): Promise<any[]> {
  manifestPromise ??= fetch(researchVersionedHref('/research/catalogue/manifest.json'), { cache: 'no-store' })
    .then(response => {
      if (!response.ok) throw new Error(`Research catalogue manifest failed: ${response.status}`);
      return response.json() as Promise<CatalogueManifest>;
    }).catch(error => { manifestPromise = null; throw error; });
  const manifest = await manifestPromise;
  if (!Array.isArray(manifest.chunks)) throw new Error('Invalid research catalogue manifest');
  const allowedFacets = new Set<CatalogueScope>(['disease', 'country', 'topic', 'study', 'week']);
  const requested = Object.entries(facets ?? {}).filter(([key, candidate]) => allowedFacets.has(key as CatalogueScope) && typeof candidate === 'string' && candidate.trim()) as Array<[CatalogueScope, string]>;
  if (scope && allowedFacets.has(scope) && typeof value === 'string' && value.trim()) requested.push([scope, value]);
  // Missing or malformed old metadata cannot prove a chunk is irrelevant.
  // These are safe candidate prefilters; callers still filter actual records.
  let selected = manifest.chunks.filter(chunk => (includeBibliographic || chunk.reviewed_count !== 0) && requested.every(([key, candidate]) => {
    const values = chunk.facets?.[key];
    if (!Array.isArray(values) || !values.every(item => typeof item === 'string')) return true;
    return values.some(item => item.trim().toLowerCase() === candidate.trim().toLowerCase());
  }));
  if (limit !== undefined) {
    let count = 0;
    selected = selected.filter(chunk => {
      if (count >= limit) return false;
      count += chunk.count;
      return true;
    });
  }
  let next = 0, completed = 0;
  const chunks: any[][] = new Array(selected.length);
  onProgress?.({ mode: 'catalogue', completed, total: selected.length });
  await Promise.all(Array.from({ length: Math.min(researchCatalogueConcurrency, selected.length) }, async () => {
    for (let position = next++; position < selected.length; position = next++) {
      const chunk = selected[position];
      let promise = chunkPromises.get(chunk.path);
      if (!promise) {
        promise = queueChunkRequest(async () => {
          const response = await fetch(researchVersionedHref(chunk.path));
          if (!response.ok) throw new Error(`Research catalogue chunk failed: ${response.status}`);
          const payload = await response.json();
          if (!Array.isArray(payload.articles) || payload.articles.length !== chunk.count) throw new Error('Incomplete research catalogue chunk');
          return payload.articles as any[];
        }).catch(error => { chunkPromises.delete(chunk.path); throw error; });
        chunkPromises.set(chunk.path, promise);
      }
      chunks[position] = await promise;
      onProgress?.({ mode: 'catalogue', completed: ++completed, total: selected.length });
    }
  }));
  return chunks.flat().filter(article => includeBibliographic || article.detail_available !== false);
}

let entityPromise: Promise<ResearchQueryEntity[]> | undefined;
/** Resolve scoped questions from the shared catalogue, with exact membership checks. */
export async function loadResearchQuestionCatalogue(query: string, { onProgress }: Pick<CatalogueRequest, 'onProgress'> = {}): Promise<any[]> {
  entityPromise ??= fetch(researchVersionedHref('/research/catalogue/entities.json'), { cache: 'no-cache' }).then(async response => {
    if (!response.ok) throw new Error('Research entities unavailable');
    const payload = await response.json();
    if (!Array.isArray(payload.entities)) throw new Error('Invalid research entities');
    return payload.entities as ResearchQueryEntity[];
  }).catch(error => { entityPromise = undefined; throw error; });
  const scopes = researchQueryScopes(query, await entityPromise);
  if (!scopes.length) return loadResearchCatalogue({ onProgress });
  const collections = await Promise.all(scopes.map(async scope => {
    const records = await loadResearchCatalogue({ scope: scope.scope, value: scope.value, onProgress });
    const value = scope.value.toLowerCase();
    return records.filter(article => {
      if (scope.scope === 'disease') return (article.diseases ?? []).some((item: any) => String(item.disease_id ?? '').toLowerCase() === value);
      if (scope.scope === 'country') return (article.countries ?? []).some((item: any) => String(item.code ?? '').toLowerCase() === value);
      return (article.topics ?? []).some((item: any) => String(item.name ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') === value);
    });
  }));
  const unique = new Map<string, any>();
  for (const article of collections.flat()) unique.set(String(article.article_id || article.doi || article.slug), article);
  return [...unique.values()];
}
