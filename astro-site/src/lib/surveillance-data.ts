/** Resolve every dataset on one page against the same immutable publication. */
import { mainSiteBase } from './papers.ts';
export interface SurveillanceFile { url: string; sha256: string; bytes?: number; }
export interface SurveillanceManifest {
  schema_version: 1;
  release_id: string;
  generated_at?: string;
  files: Record<string, SurveillanceFile>;
  manifest?: SurveillanceFile;
}

export const DEFAULT_SURVEILLANCE_MANIFEST_URL = '/site-data/bootstrap.json';
const configuredManifestUrl = () => {
  const value = import.meta.env?.PUBLIC_GIDS_DATA_MANIFEST_URL || DEFAULT_SURVEILLANCE_MANIFEST_URL;
  return value.startsWith('/') ? `${mainSiteBase()}${value}` : value;
};
const validKey = (value: string) => /^(?:[a-z0-9_-]+\/)*[a-z0-9_.-]+\.json$/i.test(value) && !value.split('/').some(part => part === '..');

export function createSurveillanceDataClient({ manifestUrl = configuredManifestUrl(), logicalRoot = '/site-data/', fetcher = globalThis.fetch,
  digest = async (body: ArrayBuffer) => {
    if (!globalThis.crypto?.subtle) throw new Error('Dataset integrity verification is unavailable');
    return Array.from(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', body)), byte => byte.toString(16).padStart(2, '0')).join('');
  }, baseUrl = typeof location === 'undefined' ? 'http://localhost/' : location.href } = {}) {
  const manifestLocation = new URL(manifestUrl, baseUrl);
  let publication: Promise<SurveillanceManifest | null> | undefined;
  let completePublication: Promise<SurveillanceManifest> | undefined;
  const requests = new Map<string, Promise<unknown>>();
  const validate = (value: SurveillanceManifest) => {
      if (value?.schema_version !== 1 || typeof value.release_id !== 'string' || !value.release_id
        || !value.files || Array.isArray(value.files) || typeof value.files !== 'object') throw new Error('Invalid data publication manifest');
      for (const [key, file] of [...Object.entries(value.files), ...(value.manifest ? [['manifest.json', value.manifest] as const] : [])]) {
        if (!validKey(key) || typeof file?.url !== 'string' || !/^[a-f0-9]{64}$/i.test(file.sha256)
          || (file.bytes !== undefined && (!Number.isSafeInteger(file.bytes) || file.bytes < 0))) throw new Error('Invalid data publication file');
        const target = new URL(file.url, manifestLocation);
        if (!['http:', 'https:'].includes(target.protocol) || target.username || target.password) throw new Error('Invalid data publication URL');
      }
      return value;
  };
  const manifest = () => {
    if (!publication) publication = (async () => {
      let response = await fetcher(manifestLocation.href, { cache: 'no-cache', credentials: 'same-origin' });
      if (response.status === 404 && manifestLocation.pathname.endsWith('/bootstrap.json')) {
        response = await fetcher(new URL('current.json', manifestLocation).href, { cache: 'no-cache', credentials: 'same-origin' });
      }
      if (response.status === 404) return null;
      if (!response.ok) throw new Error(`Data publication unavailable (${response.status})`);
      return validate(await response.json() as SurveillanceManifest);
    })().catch(error => { publication = undefined; throw error; });
    return publication;
  };
  const keyForUrl = (url: string) => {
    const path = new URL(url, baseUrl).pathname;
    return path.startsWith(logicalRoot) ? path.slice(logicalRoot.length) : null;
  };
  return {
    manifest,
    async read<T = Record<string, unknown>>(logicalPath: string, { signal, retain = true }: { signal?: AbortSignal; retain?: boolean } = {}): Promise<T> {
      const key = keyForUrl(logicalPath);
      if (!key || !validKey(key)) {
        const response = await fetcher(logicalPath, { signal, credentials: 'same-origin' });
        if (!response.ok) throw new Error(`Dataset unavailable (${response.status})`);
        return await response.json() as T;
      }
      if (!requests.has(key)) requests.set(key, (async () => {
        const current = await manifest();
        if (!current) {
          const response = await fetcher(logicalPath, { signal, credentials: 'same-origin', cache: 'no-cache' });
          if (!response.ok) throw new Error(`Dataset unavailable (${response.status})`);
          return response.json();
        }
        let file = current.files[key];
        if (!file && current.manifest) {
          completePublication ??= (async () => {
            const reference = current.manifest!;
            const response = await fetcher(new URL(reference.url, manifestLocation).href, { credentials: 'same-origin', cache: 'force-cache' });
            if (!response.ok) throw new Error(`Data publication unavailable (${response.status})`);
            const body = await response.arrayBuffer();
            if ((reference.bytes !== undefined && body.byteLength !== reference.bytes)
              || (await digest(body)).toLowerCase() !== reference.sha256.toLowerCase()) throw new Error('Data publication checksum mismatch');
            const full = validate(JSON.parse(new TextDecoder().decode(body)));
            if (full.release_id !== current.release_id) throw new Error('Data publication version mismatch');
            return full;
          })().catch(error => { completePublication = undefined; throw error; });
          file = (await completePublication).files[key];
        }
        if (!file) throw new Error(`Dataset absent from publication ${current.release_id}: ${key}`);
        const response = await fetcher(new URL(file.url, manifestLocation).href, { signal, credentials: 'same-origin', cache: 'force-cache' });
        if (!response.ok) throw new Error(`Dataset unavailable (${response.status})`);
        const body = await response.arrayBuffer();
        if (file.bytes !== undefined && body.byteLength !== file.bytes) throw new Error(`Dataset size mismatch: ${key}`);
        if ((await digest(body)).toLowerCase() !== file.sha256.toLowerCase()) throw new Error(`Dataset checksum mismatch: ${key}`);
        return JSON.parse(new TextDecoder().decode(body));
      })().catch(error => { requests.delete(key); throw error; }));
      const request = requests.get(key)!;
      try { return await request as T; }
      finally { if (!retain && requests.get(key) === request) requests.delete(key); }
    },
  };
}

// ES modules are shared by independently hydrated islands, so summary, curve,
// heatmap and source details stay pinned even if current.json changes mid-page.
export const surveillanceData = createSurveillanceDataClient();
