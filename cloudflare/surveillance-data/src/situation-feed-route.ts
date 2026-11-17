/** Exactly two bounded R2 reads, pinned to one verified Situation publication. */
import { renderSituationFeed, situationFeedSelection } from './situation-feed.ts';

const MANIFEST_LIMIT = 2 * 1024 * 1024;
const DATA_LIMIT = 8 * 1024 * 1024;

function headers(rss = false): Headers {
  return new Headers({
    'Content-Type': rss ? 'application/rss+xml; charset=utf-8' : 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Expose-Headers': 'ETag, X-GIDS-Situation-Release',
    'X-Content-Type-Options': 'nosniff',
  });
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

async function digest(bytes: BufferSource): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
}

async function boundedBytes(value: R2ObjectBody, limit: number): Promise<ArrayBuffer> {
  const encoding = value.httpMetadata?.contentEncoding;
  if (value.size > limit || encoding && encoding !== 'gzip') {
    await value.body.cancel();
    throw new Error('Unsupported Situation feed object size or encoding');
  }
  // The publisher hashes JSON before optional gzip compression. Bound the
  // decompressed stream as well as the stored object before verifying that hash.
  const stream = encoding === 'gzip' ? value.body.pipeThrough(new DecompressionStream('gzip')) : value.body;
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      length += next.value.byteLength;
      if (length > limit) {
        await reader.cancel();
        throw new Error('Decompressed Situation feed exceeds supported size');
      }
      chunks.push(next.value);
    }
  } finally { reader.releaseLock(); }
  if (!encoding && length !== value.size) throw new Error('Invalid Situation feed object size');
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes.buffer;
}

export async function serveSituationFeed(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: headers() });
  if (!['GET', 'HEAD'].includes(request.method)) {
    const responseHeaders = headers();
    responseHeaders.set('Allow', 'GET, HEAD, OPTIONS');
    return new Response('{"error":"method_not_allowed"}', { status: 405, headers: responseHeaders });
  }
  const selection = situationFeedSelection(url);
  if (!selection) return new Response('{"error":"invalid_feed_filter"}', { status: 400, headers: headers() });
  const prefix = (env.SITUATION_PREFIX || 'site-data/situation').replace(/^\/+|\/+$/g, '');
  const logical = selection.country ? `by-country/${selection.country}.json`
    : selection.disease ? `by-disease/${selection.disease}.json` : 'feed.json';
  try {
    const pointer = await env.DATA.get(`${prefix}/current.json`);
    if (!pointer) throw new Error('Situation publication unavailable');
    const manifest: unknown = JSON.parse(new TextDecoder().decode(await boundedBytes(pointer, MANIFEST_LIMIT)));
    if (!object(manifest) || manifest.schema_version !== 1 || typeof manifest.release_id !== 'string' || !object(manifest.files)) {
      throw new Error('Invalid Situation publication');
    }
    const entry = manifest.files[logical];
    if (!object(entry) || typeof entry.url !== 'string' || typeof entry.sha256 !== 'string'
      || !Number.isSafeInteger(entry.bytes) || Number(entry.bytes) < 0 || Number(entry.bytes) > DATA_LIMIT) {
      throw new Error('Situation feed absent or invalid');
    }
    const target = /^objects\/([a-f0-9]{64})\.json$/.exec(entry.url);
    if (!target || target[1] !== entry.sha256) throw new Error('Invalid immutable Situation feed reference');
    const input = await env.DATA.get(`${prefix}/${target[0]}`);
    if (!input) throw new Error('Situation feed object unavailable');
    const bytes = await boundedBytes(input, DATA_LIMIT);
    if (bytes.byteLength !== entry.bytes || await digest(bytes) !== entry.sha256) throw new Error('Situation feed integrity mismatch');
    const body = renderSituationFeed(JSON.parse(new TextDecoder().decode(bytes)), url.origin, selection);
    const etag = `"${await digest(new TextEncoder().encode(body))}"`;
    const responseHeaders = headers(true);
    responseHeaders.set('ETag', etag);
    responseHeaders.set('X-GIDS-Situation-Release', manifest.release_id);
    if (request.headers.get('If-None-Match')?.split(',').map(value => value.trim()).includes(etag)) {
      return new Response(null, { status: 304, headers: responseHeaders });
    }
    return new Response(request.method === 'HEAD' ? null : body, { headers: responseHeaders });
  } catch (error) {
    console.error(JSON.stringify({ event: 'situation_feed_unavailable', path: logical,
      error_type: error instanceof Error ? error.name : 'Error' }));
    const responseHeaders = headers();
    responseHeaders.set('Retry-After', '300');
    return new Response(request.method === 'HEAD' ? null : '{"error":"situation_feed_unavailable"}', { status: 503, headers: responseHeaders });
  }
}
