/** Read a complete Research Radar generation through one pinned manifest. */
import { createSurveillanceDataClient } from './surveillance-data.ts';
import { mainSiteBase } from './papers.ts';

const logicalRoot = '/research-publication/';
export const RESEARCH_PUBLICATION_MANIFEST = `${mainSiteBase()}/site-data/research-publication/current.json`;

export function createResearchPublicationClient(options: Parameters<typeof createSurveillanceDataClient>[0] = {}) {
  const data = createSurveillanceDataClient({
    ...options,
    manifestUrl: options.manifestUrl ?? RESEARCH_PUBLICATION_MANIFEST,
    logicalRoot,
  });
  const queue: Array<() => void> = [];
  let active = 0;
  const next = () => { while (active < 6 && queue.length) queue.shift()!(); };
  const load = <T>(name: string) => {
    if (!/^(?:[a-z0-9_-]+\/)*[a-z0-9_.-]+\.json$/i.test(name) || name.split('/').includes('..'))
      throw new Error('Invalid research publication path');
    return new Promise<T>((resolve, reject) => {
      queue.push(() => {
        active++;
        void data.read<T>(`${logicalRoot}${name}`).then(resolve, reject).finally(() => { active--; next(); });
      });
      next();
    });
  };

  async function expand(value: any): Promise<any> {
    if (!value || typeof value !== 'object' || value.sharded !== true) return value;
    if (value.kind === 'value' && typeof value.file === 'string') {
      const body = await load<{ value: unknown }>(value.file);
      if (!body || !Object.hasOwn(body, 'value')) throw new Error('Invalid research value shard');
      return body.value;
    }
    if (value.kind === 'list' && Array.isArray(value.chunks) && Number.isSafeInteger(value.count)) {
      const chunks = await Promise.all(value.chunks.map(async (chunk: any) => {
        if (typeof chunk?.file !== 'string' || !Number.isSafeInteger(chunk.count) || chunk.count < 0)
          throw new Error('Invalid research list descriptor');
        const body = await load<{ records: unknown[] }>(chunk.file);
        if (!Array.isArray(body?.records) || body.records.length !== chunk.count)
          throw new Error('Incomplete research list shard');
        return body.records;
      }));
      const records = chunks.flat();
      if (records.length !== value.count) throw new Error('Incomplete research list');
      return records;
    }
    if (value.kind === 'object' && value.parts && typeof value.parts === 'object' && !Array.isArray(value.parts)) {
      return Object.fromEntries(await Promise.all(Object.entries(value.parts).map(async ([key, child]) => [key, await expand(child)])));
    }
    throw new Error('Invalid research shard descriptor');
  }

  return {
    manifest: data.manifest,
    read: load,
    async paper(path: string): Promise<string> {
      const logical = path.replace(/^\/+/, '');
      if (!/^(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]*\/?$/.test(logical) || !logical
          || logical.split('/').some(part => part === '.' || part === '..'))
        throw new Error('Invalid research paper path');
      const manifest = await data.manifest() as (Awaited<ReturnType<typeof data.manifest>> & {
        papers?: { papers_generation_sha256?: string };
      });
      const generation = manifest?.papers?.papers_generation_sha256;
      if (typeof generation !== 'string' || !/^[a-f0-9]{64}$/.test(generation))
        throw new Error('Research paper generation unavailable');
      return `${mainSiteBase()}/research-publication/${generation}/${logical}`;
    },
    async index(): Promise<Record<string, any>> {
      const index = await load<Record<string, any>>('index.json');
      if (!index || Array.isArray(index) || typeof index !== 'object') throw new Error('Invalid research index');
      return index;
    },
    field: (index: Record<string, any>, key: string) => expand(index[key]),
  };
}
