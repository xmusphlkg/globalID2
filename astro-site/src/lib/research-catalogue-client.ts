import { researchVersionedHref, siteHref } from './papers.ts';
import { researchQueryScopes, type ResearchQueryEntity } from './research-query-scope.ts';

type CatalogueScope = 'disease' | 'country' | 'topic' | 'study' | 'week';
export type CatalogueFacets = Partial<Record<CatalogueScope, string>>;
type CatalogueChunk = { path: string; count: number; study?: string; facets?: Partial<Record<CatalogueScope, string[]>> };
type CatalogueManifest = { chunks: CatalogueChunk[]; study_collections?: Record<string, { count: number; chunks: Array<{ path: string; count: number }> }> };
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
  let selected = manifest.chunks.filter(chunk => requested.every(([key, candidate]) => {
    const values = chunk.facets?.[key];
    if (!Array.isArray(values) || !values.every(item => typeof item === 'string')) return true;
    return values.some(item => item.trim().toLowerCase() === candidate.trim().toLowerCase());
  }));
  const requestedStudies = requested.filter(([key]) => key === 'study').map(([, candidate]) => candidate.trim().toLowerCase());
  const study = requestedStudies[0];
  const studyCollection = study && requestedStudies.every(value => value === study) ? manifest.study_collections?.[study] : undefined;
  // A study collection is complete; other facets remain exact client filters.
  if (studyCollection && Number.isSafeInteger(studyCollection.count) && studyCollection.count >= 0
    && Array.isArray(studyCollection.chunks) && studyCollection.chunks.every(chunk => typeof chunk.path === 'string'
      && chunk.path.startsWith('/research/catalogue/studies/') && Number.isSafeInteger(chunk.count) && chunk.count >= 0)
    && studyCollection.chunks.reduce((sum, chunk) => sum + chunk.count, 0) === studyCollection.count) {
    selected = studyCollection.chunks.map(chunk => ({ path: siteHref(chunk.path), count: chunk.count, study }));
  }
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
          if (chunk.study && String(payload.study ?? '').trim().toLowerCase() !== chunk.study) throw new Error('Mismatched research study collection');
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
const scopedPromises = new Map<string, Promise<any[]>>();
/** Ask searches the complete disease/region/topic collection, not a date-limited preview. */
export async function loadResearchQuestionCatalogue(query: string, { onProgress }: Pick<CatalogueRequest, 'onProgress'> = {}): Promise<any[]> {
  entityPromise ??= fetch(researchVersionedHref('/research/catalogue/entities.json'), { cache: 'no-cache' }).then(async response => {
    if (!response.ok) throw new Error('Research entities unavailable');
    const payload = await response.json();
    if (!Array.isArray(payload.entities)) throw new Error('Invalid research entities');
    return payload.entities as ResearchQueryEntity[];
  }).catch(error => { entityPromise = undefined; throw error; });
  const scopes = researchQueryScopes(query, await entityPromise);
  if (!scopes.length) return loadResearchCatalogue({ onProgress });
  let completed = 0;
  onProgress?.({ mode: 'collection', completed, total: scopes.length });
  const collections = await Promise.all(scopes.map(async scope => {
    let promise = scopedPromises.get(scope.path);
    if (!promise) {
      promise = fetch(siteHref(scope.path), { cache: 'no-cache' }).then(async response => {
        if (!response.ok) throw new Error('Research collection unavailable');
        const payload = await response.json();
        if (!Array.isArray(payload.articles)) throw new Error('Invalid research collection');
        return payload.articles as any[];
      }).catch(error => { scopedPromises.delete(scope.path); throw error; });
      scopedPromises.set(scope.path, promise);
    }
    const records = await promise;
    onProgress?.({ mode: 'collection', completed: ++completed, total: scopes.length });
    return records;
  }));
  const unique = new Map<string, any>();
  for (const article of collections.flat()) unique.set(String(article.article_id || article.doi || article.slug), article);
  return [...unique.values()];
}
