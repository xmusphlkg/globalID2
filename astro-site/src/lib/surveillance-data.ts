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
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;
const configuredManifestUrl = () => {
  const value = import.meta.env?.PUBLIC_GIDS_DATA_MANIFEST_URL || DEFAULT_SURVEILLANCE_MANIFEST_URL;
  return value.startsWith('/') ? `${mainSiteBase()}${value}` : value;
};
const validKey = (value: string) => /^(?:[a-z0-9_-]+\/)*[a-z0-9_.-]+\.json$/i.test(value) && !value.split('/').some(part => part === '..');

// A subscriber owns its wait, not the shared network request. Keep a rejection
// handler attached after cancellation so a later network failure is observed.
function waitForSignal<T>(request: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return request;
  if (signal.aborted) {
    void request.catch(() => undefined);
    return Promise.reject(signal.reason);
  }
  return new Promise<T>((resolve, reject) => {
    const aborted = () => { signal.removeEventListener('abort', aborted); reject(signal.reason); };
    signal.addEventListener('abort', aborted, { once: true });
    request.then(value => {
      signal.removeEventListener('abort', aborted);
      resolve(value);
    }, error => {
      signal.removeEventListener('abort', aborted);
      reject(error);
    });
  });
}

interface DatasetRequest {
  promise: Promise<unknown>;
  consumers: number;
  retained: boolean;
  settled: boolean;
}

export function createSurveillanceDataClient({ manifestUrl = configuredManifestUrl(), logicalRoot = '/site-data/', fetcher = globalThis.fetch,
  digest = async (body: ArrayBuffer) => {
    if (!globalThis.crypto?.subtle) throw new Error('Dataset integrity verification is unavailable');
    return Array.from(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', body)), byte => byte.toString(16).padStart(2, '0')).join('');
  }, baseUrl = typeof location === 'undefined' ? 'http://localhost/' : location.href,
  requestTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS } = {}) {
  if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0 || requestTimeoutMs > 2_147_483_647) {
    throw new RangeError('Dataset request timeout must be a positive finite timer duration');
  }
  const manifestLocation = new URL(manifestUrl, baseUrl);
  let publication: Promise<SurveillanceManifest | null> | undefined;
  let completePublication: Promise<SurveillanceManifest> | undefined;
  const requests = new Map<string, DatasetRequest>();
  const fetchAndRead = async <T>(url: string, init: RequestInit, read: (response: Response) => Promise<T>, signal?: AbortSignal): Promise<T> => {
    signal?.throwIfAborted();
    const controller = new AbortController();
    const aborted = () => controller.abort(signal?.reason);
    signal?.addEventListener('abort', aborted, { once: true });
    // Keep the deadline through body reads and integrity checks. Racing the
    // signal also bounds fetch implementations that do not honor cancellation.
    const timer = globalThis.setTimeout(() => controller.abort(new DOMException('Dataset request timed out', 'TimeoutError')), requestTimeoutMs);
    try {
      return await waitForSignal((async () => read(await fetcher(url, { ...init, signal: controller.signal })))(), controller.signal);
    } finally {
      globalThis.clearTimeout(timer);
      signal?.removeEventListener('abort', aborted);
    }
  };
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
  const fetchManifest = (url: string) => fetchAndRead(url, { cache: 'no-cache', credentials: 'same-origin' }, async response => {
      if (response.status === 404) return null;
      if (!response.ok) throw new Error(`Data publication unavailable (${response.status})`);
      return validate(await response.json() as SurveillanceManifest);
  });
  const manifest = () => {
    if (!publication) {
      const request = (async () => {
        const value = await fetchManifest(manifestLocation.href);
        return value === null && manifestLocation.pathname.endsWith('/bootstrap.json')
          ? fetchManifest(new URL('current.json', manifestLocation).href) : value;
      })().catch(error => { if (publication === request) publication = undefined; throw error; });
      publication = request;
    }
    return publication;
  };
  const fullManifest = (current: SurveillanceManifest) => {
    if (!completePublication) {
      const reference = current.manifest!;
      const request = fetchAndRead(new URL(reference.url, manifestLocation).href, { credentials: 'same-origin', cache: 'force-cache' }, async response => {
        if (!response.ok) throw new Error(`Data publication unavailable (${response.status})`);
        const body = await response.arrayBuffer();
        if ((reference.bytes !== undefined && body.byteLength !== reference.bytes)
          || (await digest(body)).toLowerCase() !== reference.sha256.toLowerCase()) throw new Error('Data publication checksum mismatch');
        const full = validate(JSON.parse(new TextDecoder().decode(body)));
        if (full.release_id !== current.release_id) throw new Error('Data publication version mismatch');
        return full;
      }).catch(error => { if (completePublication === request) completePublication = undefined; throw error; });
      completePublication = request;
    }
    return completePublication;
  };
  const readJson = async (response: Response) => {
    if (!response.ok) throw new Error(`Dataset unavailable (${response.status})`);
    return response.json();
  };
  const readDataset = async (key: string, logicalPath: string) => {
    const current = await manifest();
    if (!current) return fetchAndRead(logicalPath, { credentials: 'same-origin', cache: 'no-cache' }, readJson);
    const file = current.files[key] ?? (current.manifest ? (await fullManifest(current)).files[key] : undefined);
    if (!file) throw new Error(`Dataset absent from publication ${current.release_id}: ${key}`);
    return fetchAndRead(new URL(file.url, manifestLocation).href, { credentials: 'same-origin', cache: 'force-cache' }, async response => {
      if (!response.ok) throw new Error(`Dataset unavailable (${response.status})`);
      const body = await response.arrayBuffer();
      if (file.bytes !== undefined && body.byteLength !== file.bytes) throw new Error(`Dataset size mismatch: ${key}`);
      if ((await digest(body)).toLowerCase() !== file.sha256.toLowerCase()) throw new Error(`Dataset checksum mismatch: ${key}`);
      return JSON.parse(new TextDecoder().decode(body));
    });
  };
  const release = (key: string, request: DatasetRequest) => {
    if (request.settled && request.consumers === 0 && !request.retained && requests.get(key) === request) requests.delete(key);
  };
  const keyForUrl = (url: string) => {
    const path = new URL(url, baseUrl).pathname;
    return path.startsWith(logicalRoot) ? path.slice(logicalRoot.length) : null;
  };
  return {
    manifest({ signal }: { signal?: AbortSignal } = {}) {
      if (signal?.aborted) return Promise.reject(signal.reason);
      return waitForSignal(manifest(), signal);
    },
    async read<T = Record<string, unknown>>(logicalPath: string, { signal, retain = true }: { signal?: AbortSignal; retain?: boolean } = {}): Promise<T> {
      signal?.throwIfAborted();
      const key = keyForUrl(logicalPath);
      if (!key || !validKey(key)) {
        return fetchAndRead(logicalPath, { credentials: 'same-origin' }, readJson, signal) as Promise<T>;
      }
      if (!requests.has(key)) {
        const request: DatasetRequest = { promise: readDataset(key, logicalPath), consumers: 0, retained: false, settled: false };
        request.promise = request.promise.then(value => {
          request.settled = true;
          release(key, request);
          return value;
        }, error => {
          request.settled = true;
          if (requests.get(key) === request) requests.delete(key);
          throw error;
        });
        requests.set(key, request);
      }
      const request = requests.get(key)!;
      request.consumers++;
      try {
        const value = await waitForSignal(request.promise, signal);
        if (retain) request.retained = true;
        return value as T;
      } finally {
        request.consumers--;
        release(key, request);
      }
    },
  };
}

// ES modules are shared by independently hydrated islands, so summary, curve,
// heatmap and source details stay pinned even if current.json changes mid-page.
export const surveillanceData = createSurveillanceDataClient();
