/** Assemble an immutable catalogue's public article without a second pointer. */
import { assemblePaper, type PaperRecord, type PaperTemplate } from './papers-template.ts';

const HASH = /^[a-f0-9]{64}$/;
const PREFIX = 'paper-template-v2';
const encoder = new TextEncoder();
const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

async function digest(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
    .map(value => value.toString(16).padStart(2, '0')).join('');
}

async function boundedBytes(stream: ReadableStream<Uint8Array>, limit: number): Promise<Uint8Array<ArrayBuffer>> {
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      length += result.value.byteLength;
      if (length > limit) {
        await reader.cancel();
        throw new Error('Paper template object exceeds decoded size limit');
      }
      chunks.push(result.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return bytes;
}

async function immutableJson(request: Request, env: Env, group: 'templates' | 'records',
                             sha256: string, limit: number, ctx?: ExecutionContext): Promise<unknown> {
  const key = `${PREFIX}/${group}/${sha256}.json`;
  const cache = typeof caches === 'undefined' ? undefined : caches.default;
  const url = new URL(request.url);
  url.pathname = `/.paper-template-cache/${key}`;
  url.search = '';
  const cacheKey = new Request(url.toString());
  let cached: Response | undefined;
  try { cached = await cache?.match(cacheKey); }
  catch (cause) { console.error(JSON.stringify({ event: 'paper_template_cache_read_failed',
    error_type: cause instanceof Error ? cause.name : 'Error' })); }
  let bytes: Uint8Array<ArrayBuffer>;
  if (cached?.body) bytes = await boundedBytes(cached.body, limit);
  else {
    const object = await env.DATA.get(key);
    if (!object || object.size > limit) throw new Error('Missing or oversized paper template object');
    const encoding = object.httpMetadata?.contentEncoding;
    if (encoding && encoding !== 'gzip') throw new Error('Unsupported paper template encoding');
    const stream = encoding === 'gzip' ? object.body.pipeThrough(new DecompressionStream('gzip')) : object.body;
    bytes = await boundedBytes(stream, limit);
  }
  if (await digest(bytes) !== sha256) throw new Error('Paper template object integrity failure');
  const value: unknown = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(bytes));
  if (!cached && cache) {
    const write = cache.put(cacheKey, new Response(bytes, { headers: {
      'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=31536000, immutable',
    } })).catch(cause => console.error(JSON.stringify({ event: 'paper_template_cache_write_failed',
      error_type: cause instanceof Error ? cause.name : 'Error' })));
    if (ctx) ctx.waitUntil(write); else await write;
  }
  return value;
}

export async function templateArticleResponse(request: Request, env: Env, entry: Record<string, unknown>,
                                              logicalKey: string, generation: string,
                                              ctx?: ExecutionContext): Promise<Response> {
  const path = /^(?:(zh|fr)\/)?research\/articles\/([a-z0-9][a-z0-9-]*)\/$/.exec(logicalKey);
  const descriptor = entry.paper_template;
  if (!path || !isObject(descriptor) || descriptor.locale !== (path[1] || 'en') || descriptor.slug !== path[2]
      || typeof descriptor.template_sha256 !== 'string' || !HASH.test(descriptor.template_sha256)
      || typeof descriptor.record_sha256 !== 'string' || !HASH.test(descriptor.record_sha256)
      || entry.key !== `${PREFIX}/records/${descriptor.record_sha256}.json`
      || typeof entry.content_type !== 'string' || !/^text\/html(?:;|$)/i.test(entry.content_type)
      || typeof entry.sha256 !== 'string' || !HASH.test(entry.sha256)
      || typeof entry.bytes !== 'number' || !Number.isSafeInteger(entry.bytes) || entry.bytes < 0
      || entry.bytes > 3 * 1024 * 1024) throw new Error('Invalid paper template catalogue entry');
  const [rawTemplate, rawRecord] = await Promise.all([
    immutableJson(request, env, 'templates', descriptor.template_sha256, 1024 * 1024, ctx),
    immutableJson(request, env, 'records', descriptor.record_sha256, 2 * 1024 * 1024, ctx),
  ]);
  if (!isObject(rawTemplate) || !isObject(rawRecord) || rawRecord.schema !== 2
      || rawRecord.slug !== descriptor.slug || !isObject(rawRecord.locales)) throw new Error('Invalid paper template identity');
  const localized = rawRecord.locales[descriptor.locale];
  if (!isObject(localized)) throw new Error('Missing localized paper template content');
  const record = { schema: 1, slug: descriptor.slug, head: localized.head, main: localized.main } as PaperRecord;
  const html = assemblePaper(rawTemplate as PaperTemplate, record);
  const encoded = new Uint8Array(encoder.encode(html));
  if (encoded.byteLength !== entry.bytes || await digest(encoded) !== entry.sha256)
    throw new Error('Assembled paper differs from published catalogue');
  const etag = `"${generation}-${entry.sha256}"`;
  const headers = new Headers({ 'Content-Type': entry.content_type, 'Cache-Control': 'public, max-age=31536000, immutable',
    'ETag': etag, 'X-Content-Type-Options': 'nosniff', 'Access-Control-Allow-Origin': '*',
    'X-GIDS-Paper-Mode': 'template', 'X-GIDS-Paper-SHA256': entry.sha256 });
  if (request.headers.get('If-None-Match')?.split(',').map(value => value.trim()).includes(etag))
    return new Response(null, { status: 304, headers });
  return new Response(request.method === 'HEAD' ? null : html, { headers });
}
