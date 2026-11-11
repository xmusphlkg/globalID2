import { siteHref } from './papers.ts';
import { literatureRecordId } from './research-literature.ts';

export interface LiteratureShard { id: string; url: string; count: number; }
export interface LiteratureIndex { schema_version: 2; disease_id: string; total: number; page_size: number; records: any[]; shards: LiteratureShard[]; }

/** One bounded queue and LRU per library view. Failed pages remain retryable. */
export function createLiteraturePageClient({ fetcher = globalThis.fetch, concurrency = 4, cacheSize = 48 } = {}) {
  const cache = new Map<string, Promise<any[]>>();
  const settled = new Set<string>();
  const queue: Array<() => void> = [];
  let active = 0;
  const next = () => { while (active < concurrency && queue.length) queue.shift()!(); };
  const load = (diseaseId: string, shard: LiteratureShard) => {
    const cached = cache.get(shard.url);
    if (cached) { cache.delete(shard.url); cache.set(shard.url, cached); return cached; }
    const promise = new Promise<any[]>((resolve, reject) => {
      queue.push(() => {
        active++;
        void fetcher(siteHref(shard.url), { cache: 'force-cache', signal: AbortSignal.timeout(15_000) })
          .then(async response => {
            if (!response.ok) throw new Error('Literature page unavailable');
            const value = await response.json();
            if (value.schema_version !== 2 || value.disease_id !== diseaseId || value.shard !== shard.id || !Array.isArray(value.articles) || value.articles.length !== shard.count) throw new Error('Incomplete literature page');
            resolve(value.articles);
          }).catch(reject).finally(() => { active--; next(); });
      });
    }).then(value => {
      settled.add(shard.url);
      for (const key of cache.keys()) {
        if (cache.size <= cacheSize) break;
        if (settled.has(key)) { cache.delete(key); settled.delete(key); }
      }
      return value;
    }).catch(error => { cache.delete(shard.url); settled.delete(shard.url); throw error; });
    cache.set(shard.url, promise);
    next();
    return promise;
  };
  return {
    async read(index: LiteratureIndex, records: any[]): Promise<any[]> {
      const ids = new Set(records.map(record => record.shard));
      const shards = index.shards.filter(shard => ids.has(shard.id));
      if (shards.length !== ids.size) throw new Error('Literature index references a missing page');
      const entries = new Map((await Promise.all(shards.map(shard => load(index.disease_id, shard)))).flat().map(article => [literatureRecordId(article), article]));
      return records.map(record => {
        const article = entries.get(literatureRecordId(record));
        if (!article) throw new Error('Literature page is incomplete');
        return article;
      });
    },
  };
}
