import { researchVersionedHref } from './papers.ts';
import { researchQueryScopes, type ResearchQueryEntity } from './research-query-scope.ts';

type CatalogueScope = 'disease' | 'country' | 'topic' | 'study' | 'week';
export type CatalogueFacets = Partial<Record<CatalogueScope, string>>;
type CatalogueChunk = { path: string; count: number; reviewed_count?: number; facets?: Partial<Record<CatalogueScope, string[]>> };
type CatalogueManifest = { chunks: CatalogueChunk[]; disease_counts?: Record<string, number> };
export type CatalogueProgress = { mode: 'catalogue' | 'collection'; completed: number; total: number };
type CatalogueRequest = { limit?: number; scope?: CatalogueScope; value?: string; facets?: CatalogueFacets; includeBibliographic?: boolean; signal?: AbortSignal; onProgress?: (progress: CatalogueProgress) => void };
export const researchCatalogueConcurrency = 4;

/** Cancel one caller's wait without aborting a fetch shared with other callers. */
function waitForCatalogueRequest<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const aborted = () => reject(signal.reason);
    signal.addEventListener('abort', aborted, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', aborted));
  });
}

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

async function selectCatalogueChunks({ scope, value, facets, includeBibliographic, signal }: CatalogueRequest) {
  signal?.throwIfAborted();
  manifestPromise ??= fetch(researchVersionedHref('/research/catalogue/manifest.json'), { cache: 'no-store' })
    .then(response => {
      if (!response.ok) throw new Error(`Research catalogue manifest failed: ${response.status}`);
      return response.json() as Promise<CatalogueManifest>;
    }).catch(error => { manifestPromise = null; throw error; });
  const manifest = await waitForCatalogueRequest(manifestPromise, signal);
  if (!Array.isArray(manifest.chunks)) throw new Error('Invalid research catalogue manifest');
  const allowedFacets = new Set<CatalogueScope>(['disease', 'country', 'topic', 'study', 'week']);
  const requested = Object.entries(facets ?? {}).filter(([key, candidate]) => allowedFacets.has(key as CatalogueScope) && typeof candidate === 'string' && candidate.trim()) as Array<[CatalogueScope, string]>;
  if (scope && allowedFacets.has(scope) && typeof value === 'string' && value.trim()) requested.push([scope, value]);
  // Missing or malformed old metadata cannot prove a chunk is irrelevant.
  // These are safe candidate prefilters; callers still filter actual records.
  const chunks = manifest.chunks.filter(chunk => (includeBibliographic || chunk.reviewed_count !== 0) && requested.every(([key, candidate]) => {
    const values = chunk.facets?.[key];
    if (!Array.isArray(values) || !values.every(item => typeof item === 'string')) return true;
    return values.some(item => item.trim().toLowerCase() === candidate.trim().toLowerCase());
  }));
  return { chunks, diseaseCounts: manifest.disease_counts };
}

function readCatalogueChunk(chunk: CatalogueChunk, signal?: AbortSignal) {
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
  return waitForCatalogueRequest(promise, signal);
}

/** Keep the position and matching rows for one set of filters. A failed read can
 * resume at its chunk, and returning to an earlier page requires no requests. */
export function createResearchCataloguePager(facets: CatalogueFacets, matches: (article: any) => boolean, pageSize: number, onlyDisease?: string) {
  const rows: any[] = [];
  let selection: Awaited<ReturnType<typeof selectCatalogueChunks>> | undefined;
  let nextChunk = 0;
  return async (requestedPage: number, signal: AbortSignal) => {
    signal.throwIfAborted();
    selection ??= await selectCatalogueChunks({ facets, includeBibliographic: true, signal });
    const { chunks, diseaseCounts } = selection;
    const knownTotal = onlyDisease ? diseaseCounts?.[onlyDisease] : undefined;
    const end = requestedPage * pageSize;
    const target = Math.min(end, knownTotal ?? end);
    let batchSize = 1;
    while (rows.length < target && nextChunk < chunks.length) {
      const batch = chunks.slice(nextChunk, nextChunk + batchSize);
      const results = await Promise.all(batch.map(chunk => readCatalogueChunk(chunk, signal)));
      signal.throwIfAborted();
      for (const articles of results) rows.push(...articles.filter(matches));
      nextChunk += batch.length;
      batchSize = researchCatalogueConcurrency;
    }
    const finished = nextChunk === chunks.length;
    const total = finished ? rows.length : knownTotal;
    const page = Math.min(requestedPage, Math.max(1, Math.ceil(rows.length / pageSize)));
    return { page, articles: rows.slice((page - 1) * pageSize, page * pageSize), total,
      hasNext: total === undefined ? !finished || rows.length > end : total > page * pageSize };
  };
}

export async function loadResearchCatalogue({ limit, onProgress, ...request }: CatalogueRequest = {}): Promise<any[]> {
  const { signal, includeBibliographic } = request;
  let { chunks: selected } = await selectCatalogueChunks(request);
  if (limit !== undefined) {
    let count = 0;
    selected = selected.filter(chunk => {
      if (count >= limit) return false;
      count += chunk.count;
      return true;
    });
  }
  let next = 0, completed = 0, stopped = false;
  const chunks: any[][] = new Array(selected.length);
  onProgress?.({ mode: 'catalogue', completed, total: selected.length });
  await Promise.all(Array.from({ length: Math.min(researchCatalogueConcurrency, selected.length) }, async () => {
    try {
      while (!stopped && next < selected.length) {
        signal?.throwIfAborted();
        const position = next++;
        const chunk = selected[position];
        chunks[position] = await readCatalogueChunk(chunk, signal);
        // A sibling may have failed, or the caller may have changed filters,
        // while this shared chunk was in flight. Do not schedule another batch.
        if (stopped) return;
        signal?.throwIfAborted();
        onProgress?.({ mode: 'catalogue', completed: ++completed, total: selected.length });
      }
    } catch (error) {
      stopped = true;
      throw error;
    }
  }));
  signal?.throwIfAborted();
  return chunks.flat().filter(article => includeBibliographic || article.detail_available !== false);
}

let entityPromise: Promise<ResearchQueryEntity[]> | undefined;
/** Resolve scoped questions from the shared catalogue, with exact membership checks. */
export async function loadResearchQuestionCatalogue(query: string, { onProgress, signal }: Pick<CatalogueRequest, 'onProgress' | 'signal'> = {}): Promise<any[]> {
  signal?.throwIfAborted();
  entityPromise ??= fetch(researchVersionedHref('/research/catalogue/entities.json'), { cache: 'no-cache' }).then(async response => {
    if (!response.ok) throw new Error('Research entities unavailable');
    const payload = await response.json();
    if (!Array.isArray(payload.entities)) throw new Error('Invalid research entities');
    return payload.entities as ResearchQueryEntity[];
  }).catch(error => { entityPromise = undefined; throw error; });
  const scopes = researchQueryScopes(query, await waitForCatalogueRequest(entityPromise, signal));
  if (!scopes.length) return loadResearchCatalogue({ onProgress, signal });
  const collections = await Promise.all(scopes.map(async scope => {
    const records = await loadResearchCatalogue({ scope: scope.scope, value: scope.value, onProgress, signal });
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
