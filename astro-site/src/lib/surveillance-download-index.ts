import { surveillanceData, type createSurveillanceDataClient } from './surveillance-data.ts';
import { validateDownloadCatalogue } from './surveillance-downloads.ts';

type Row = Record<string, any>;
type Client = Pick<ReturnType<typeof createSurveillanceDataClient>, 'manifest' | 'read'>;
const object = (value: unknown): value is Row => value !== null && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;

/** A directory reads two bounded datasets from the same pinned publication. */
export async function readSurveillanceDownloadIndex({ client = surveillanceData, signal }: { client?: Client; signal?: AbortSignal } = {}) {
  const manifest = await client.manifest({ signal });
  if (!manifest) return null;
  const [index, meta] = await Promise.all([
    client.read<Row>('/site-data/downloads/index.json', { signal }),
    client.read<Row>('/site-data/meta.json', { signal }),
  ]);
  if (!object(index) || index.schema_version !== 1 || typeof index.generated_at !== 'string'
    || !Number.isFinite(Date.parse(index.generated_at)) || !object(meta) || !Array.isArray(meta.countries)) {
    throw new Error('Invalid download directory');
  }
  for (const kind of ['countries', 'diseases']) {
    if (!Array.isArray(index[kind]) || index[kind].length > 5000) throw new Error('Invalid download directory entries');
    const ids = new Set<string>();
    for (const entry of index[kind]) {
      if (!object(entry)) throw new Error('Invalid download directory entry');
      const id = String(entry[kind === 'countries' ? 'code' : 'disease_id'] ?? '').toLowerCase();
      if (!/^[a-z0-9-]+$/.test(id) || ids.has(id) || !count(entry.parts_count)
        || !Array.isArray(entry.parts) || entry.parts.length > 1 || entry.parts_count < entry.parts.length
        || (kind === 'diseases' && (typeof entry.slug !== 'string' || !entry.slug.trim()))) {
        throw new Error('Invalid compact download directory entry');
      }
      validateDownloadCatalogue(entry, { kind, id });
      ids.add(id);
    }
  }
  return { index, meta, version: manifest.release_id };
}

