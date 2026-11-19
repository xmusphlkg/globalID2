import { downloadPartKey } from './download-presentation.ts';
import { mainSiteBase } from './papers.ts';
import { surveillanceData, type createSurveillanceDataClient } from './surveillance-data.ts';

type Row = Record<string, any>;
type Client = Pick<ReturnType<typeof createSurveillanceDataClient>, 'manifest' | 'read'>;
const object = (value: unknown): value is Row => value !== null && typeof value === 'object' && !Array.isArray(value);
const count = (value: unknown) => Number.isSafeInteger(value) && Number(value) >= 0;
const formats = ['csv', 'json', 'xlsx'];

function catalogue(value: unknown, identity?: { kind: string; id: string }): Row {
  if (!object(value) || !Array.isArray(value.parts) || !count(value.record_count)
    || typeof value.generated_at !== 'string' || !Number.isFinite(Date.parse(value.generated_at))) {
    throw new Error('Invalid download catalogue');
  }
  if (identity) {
    const key = identity.kind === 'countries' ? 'code' : 'disease_id';
    if (String(value[key] ?? '').toLowerCase() !== identity.id
      || (value.id !== undefined && String(value.id).toLowerCase() !== identity.id)
      || (value.kind !== undefined && value.kind !== (identity.kind === 'countries' ? 'country' : 'disease'))) {
      throw new Error('Download catalogue identity mismatch');
    }
  } else if (value.schema_version !== 1) throw new Error('Invalid legacy download catalogue');
  const ids = new Set<string>();
  for (const part of value.parts) {
    if (!object(part) || typeof part.id !== 'string' || !part.id.trim()
      || !count(part.record_count) || !object(part.files)) throw new Error('Invalid download part');
    for (const field of ['country_code', 'disease_id']) {
      if (part[field] !== undefined && (typeof part[field] !== 'string' || !/^[a-z0-9-]+$/i.test(part[field]))) {
        throw new Error('Invalid download part identity');
      }
    }
    const key = downloadPartKey(part);
    if (ids.has(key)) throw new Error('Invalid download part');
    ids.add(key);
    for (const format of formats) {
      const file = part.files[format];
      if (file === undefined) continue;
      if (!object(file) || typeof file.url !== 'string') throw new Error('Invalid download file');
      const url = new URL(file.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password
        || (file.bytes !== undefined && !count(file.bytes))
        || (file.filename !== undefined && typeof file.filename !== 'string')) throw new Error('Invalid download file');
    }
  }
  // Embedded entries use the native schema-4/5 manifest entry shape, without
  // their own schema_version. Keep the download date, never the summary date.
  return { ...value, schema_version: 1 };
}

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
      catalogue(entry, { kind, id });
      ids.add(id);
    }
  }
  return { index, meta, version: manifest.release_id };
}

export async function readSurveillanceDownloads(dataUrl: string, {
  client = surveillanceData, fetcher = globalThis.fetch, signal,
}: { client?: Client; fetcher?: typeof fetch; signal?: AbortSignal } = {}) {
  const match = /^\/site-data\/(countries|diseases)\/([a-z0-9-]+)-summary\.json$/i.exec(dataUrl);
  if (!match) throw new Error('Invalid download summary path');
  const [, kind, id] = match;
  const identity = { kind: kind.toLowerCase(), id: id.toLowerCase() };
  const manifest = await client.manifest({ signal });
  if (manifest) {
    const summary = await client.read<Row>(dataUrl, { signal });
    if (!object(summary) || summary.schema_version !== 1) throw new Error('Invalid download summary');
    const summaryKey = identity.kind === 'countries' ? 'country_code' : 'disease_id';
    if (String(summary[summaryKey] ?? '').toLowerCase() !== identity.id) throw new Error('Download summary identity mismatch');
    if (Object.hasOwn(summary, 'download')) {
      // Explicit null means this publication has no matching download. Network,
      // integrity and shape errors must never silently fall back to stale HTML.
      return { data: catalogue(summary.download, identity), version: manifest.release_id, source: 'publication' as const };
    }
  }
  // Compatibility is limited to deployments without a data manifest, or an
  // older summary contract that does not declare the download field at all.
  const url = `${mainSiteBase()}/site-data/downloads/${identity.kind}/${identity.id}.json`;
  const response = await fetcher(url, { cache: 'no-cache', credentials: 'same-origin', signal });
  if (!response.ok) throw new Error('Download catalogue unavailable');
  return { data: catalogue(await response.json()), version: undefined, source: 'legacy' as const };
}
