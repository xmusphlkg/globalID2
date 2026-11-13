/** Assemble an immutable catalogue's public article without a second pointer. */
import { assemblePaper } from './papers-template.ts';
import { PROJECTION_RENDERER_REVISION, projectionFragments, type ProjectedPaper, type ProjectionTemplate } from './paper-projection.ts';

const HASH = /^[a-f0-9]{64}$/;
const PREFIX = 'paper-template-v3';
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

async function immutableJson(request: Request, env: Env, group: 'templates' | 'records' | 'assets',
                             sha256: string, limit: number, ctx?: ExecutionContext, prefix = PREFIX): Promise<unknown> {
  const key = `${prefix}/${group}/${sha256}.json`;
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
  if (isObject(entry.paper_template) && entry.paper_template.schema === 3)
    return projectedArticleResponse(request, env, entry, logicalKey, generation, ctx);
  throw new Error('Paper template v2 has been removed');
}


/** V3 validates independent record, template, and asset hashes. */
async function projectedArticleResponse(request: Request, env: Env, entry: Record<string, unknown>,
                                        logicalKey: string, generation: string, ctx?: ExecutionContext): Promise<Response> {
  if (String(env.PAPERS_PROJECTION_V3) !== '1') throw new Error('Paper projection v3 is not enabled');
  const descriptor = entry.paper_template;
  const path = /^(?:(zh|fr)\/)?research\/articles\/([a-z0-9][a-z0-9-]{0,239})\/$/.exec(logicalKey);
  if (!path || !isObject(descriptor) || descriptor.locale !== (path[1] || 'en') || descriptor.slug !== path[2]
      || !['record_sha256', 'template_sha256', 'assets_sha256'].every(field => typeof descriptor[field] === 'string' && HASH.test(descriptor[field] as string))
      || entry.key !== `paper-template-v3/records/${descriptor.record_sha256}.json`
      || entry.integrity !== 'independent-v3' || typeof entry.content_type !== 'string' || !/^text\/html(?:;|$)/i.test(entry.content_type))
    throw new Error('Invalid projected paper catalogue entry');
  const recordSha = descriptor.record_sha256 as string, templateSha = descriptor.template_sha256 as string, assetsSha = descriptor.assets_sha256 as string;
  const identity = `${PROJECTION_RENDERER_REVISION}-${generation}-${recordSha}-${templateSha}-${assetsSha}-${descriptor.locale}-${await digest(new Uint8Array(encoder.encode(logicalKey)))}`;
  const etag = `"${identity}"`;
  const headers = new Headers({ 'Content-Type': entry.content_type, 'Cache-Control': 'public, max-age=31536000, immutable',
    ETag: etag, 'X-Content-Type-Options': 'nosniff', 'Access-Control-Allow-Origin': '*',
    'X-GIDS-Paper-Mode': 'projection-v3', 'X-GIDS-Paper-Content-SHA256': recordSha,
    'X-GIDS-Paper-Template-SHA256': templateSha, 'X-GIDS-Paper-Assets-SHA256': assetsSha });
  const cache = typeof caches === 'undefined' ? undefined : caches.default;
  const cacheUrl = new URL(request.url); cacheUrl.pathname = `/.paper-projection-response/${identity}`; cacheUrl.search = '';
  const cacheKey = new Request(cacheUrl);
  let cached: Response | undefined;
  try { cached = await cache?.match(cacheKey); } catch { /* Eviction or cache outage is harmless. */ }
  if (!cached) {
    const [rawTemplate, rawRecord, rawAssets] = await Promise.all([
      immutableJson(request, env, 'templates', templateSha, 1024 * 1024, ctx, 'paper-template-v3'),
      immutableJson(request, env, 'records', recordSha, 2 * 1024 * 1024, ctx, 'paper-template-v3'),
      immutableJson(request, env, 'assets', assetsSha, 1024 * 1024, ctx, 'paper-template-v3'),
    ]);
    if (!isObject(rawTemplate) || rawTemplate.locale !== descriptor.locale || !isObject(rawRecord)
        || rawRecord.slug !== descriptor.slug || !isObject(rawAssets) || rawAssets.schema !== 1 || !Array.isArray(rawAssets.items)
        || rawAssets.items.some(item => !isObject(item) || typeof item.path !== 'string' || !/^_astro\/[A-Za-z0-9._/-]+$/.test(item.path)
          || item.path.split('/').some(part => part === '..' || part === '.') || typeof item.sha256 !== 'string' || !HASH.test(item.sha256)
          || typeof item.key !== 'string' || !new RegExp(`^releases/sha256/objects/${item.sha256}/[a-f0-9]{16}$`).test(item.key)
          || !Number.isSafeInteger(item.bytes) || (item.bytes as number) < 0)) throw new Error('Invalid projected paper dependency closure');
    const template = rawTemplate as ProjectionTemplate, record = rawRecord as ProjectedPaper;
    const fragments = projectionFragments(template, record, recordSha);
    const html = assemblePaper(template.shell, { schema: 1, slug: record.slug, ...fragments });
    if (encoder.encode(html).byteLength > 3 * 1024 * 1024) throw new Error('Projected paper response exceeds limit');
    cached = new Response(html, { headers });
    if (cache) {
      const write = cache.put(cacheKey, cached.clone()).catch(() => undefined);
      if (ctx) ctx.waitUntil(write); else await write;
    }
  }
  if (request.headers.get('If-None-Match')?.split(',').map(value => value.trim()).includes(etag)) return new Response(null, { status: 304, headers });
  return new Response(request.method === 'HEAD' ? null : cached.body, { headers });
}
