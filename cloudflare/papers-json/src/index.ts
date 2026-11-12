/** Opt-in SSR for public paper records; existing R2 HTML is the fallback. */
import { assemblePaper, type PaperRecord, type PaperTemplate } from '../../surveillance-data/src/papers-template.ts';

type Binding = Pick<R2Bucket, 'get'>;
const PREFIX = 'paper-template-v2';
const HASH = /^[a-f0-9]{64}$/;
const encoder = new TextEncoder();
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);

async function digest(bytes: BufferSource): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
}
async function boundedBytes(stream: ReadableStream<Uint8Array>, limit: number): Promise<Uint8Array<ArrayBuffer>> {
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const result = await reader.read();
      if (result.done) break;
      total += result.value.byteLength;
      if (total > limit) { await reader.cancel(); throw new Error('Paper object exceeds size limit'); }
      chunks.push(result.value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result;
}
async function readJson(bucket: Binding, key: string, limit: number, expected?: string): Promise<unknown> {
  const object = await bucket.get(`${PREFIX}/${key}`);
  if (!object || object.size > limit) throw new Error('Missing or oversized paper object');
  const encoding = object.httpMetadata?.contentEncoding;
  if (encoding && encoding !== 'gzip') throw new Error('Unsupported paper object encoding');
  const stream = encoding === 'gzip' ? object.body.pipeThrough(new DecompressionStream('gzip')) : object.body;
  const bytes = await boundedBytes(stream, limit);
  if (expected && await digest(bytes) !== expected) throw new Error('Paper object integrity failure');
  return JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(bytes));
}
async function legacy(request: Request, bucket: Binding): Promise<Response> {
  const key = new URL(request.url).pathname.slice(1);
  const object = await bucket.get(key);
  if (!object) return new Response('Not found', { status: 404, headers: { 'Cache-Control': 'no-store' } });
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set('X-GIDS-Paper-Mode', 'legacy');
  return new Response(request.method === 'HEAD' ? null : object.body, {
    headers,
    // R2 supplies the stored wire bytes; do not compress gzip a second time.
    ...(object.httpMetadata?.contentEncoding ? { encodeBody: 'manual' as const } : {}),
  });
}
export async function servePaper(request: Request, env: Env): Promise<Response> {
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
  const path = /^\/(?:(zh|fr)\/)?research\/articles\/([a-z0-9][a-z0-9-]*)\/$/.exec(new URL(request.url).pathname);
  if (!path) return new Response('Not found', { status: 404 });
  const locale = path[1] || 'en', slug = path[2];
  try {
    const current = await readJson(env.DATA, 'current.json', 2048);
    if (!isObject(current) || current.schema !== 1 || typeof current.generation !== 'string' || !HASH.test(current.generation)) throw new Error('Invalid generation');
    const generation = await readJson(env.DATA, `generations/${current.generation}.json`, 262144, current.generation);
    if (!isObject(generation) || generation.schema !== 1 || !isObject(generation.locales) || !isObject(generation.indexes)) throw new Error('Invalid generation manifest');
    const spec = generation.locales[locale];
    if (!isObject(spec) || typeof spec.template !== 'string' || !HASH.test(spec.template)) throw new Error('Invalid locale');
    const bucket = (await digest(encoder.encode(slug))).slice(0, 2);
    const indexHash = generation.indexes[bucket];
    if (typeof indexHash !== 'string' || !HASH.test(indexHash)) return await legacy(request, env.DATA);
    const index = await readJson(env.DATA, `indexes/${indexHash}.json`, 2097152, indexHash);
    if (!isObject(index) || index.schema !== 1 || !isObject(index.items)) throw new Error('Invalid index');
    const recordHash = Object.prototype.hasOwnProperty.call(index.items, slug) ? index.items[slug] : null;
    if (typeof recordHash !== 'string' || !HASH.test(recordHash)) return await legacy(request, env.DATA);
    const [rawTemplate, rawRecord] = await Promise.all([
      readJson(env.DATA, `templates/${spec.template}.json`, 1048576, spec.template),
      readJson(env.DATA, `records/${recordHash}.json`, 2097152, recordHash),
    ]);
    if (!isObject(rawTemplate) || !isObject(rawRecord)) throw new Error('Invalid paper content');
    if (rawRecord.schema !== 2 || rawRecord.slug !== slug || !isObject(rawRecord.locales) || !isObject(rawRecord.locales[locale])) throw new Error('Paper identity mismatch');
    const template = rawTemplate as PaperTemplate, localized = rawRecord.locales[locale];
    const record = { schema: 1, slug, head: localized.head, main: localized.main } as PaperRecord;
    const html = assemblePaper(template, record);
    const etag = `"${spec.template}-${recordHash}"`;
    const headers = new Headers({ 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'public, max-age=0, must-revalidate', 'X-Content-Type-Options': 'nosniff', ETag: etag, 'X-GIDS-Paper-Mode': 'template' });
    if (request.headers.get('If-None-Match') === etag) return new Response(null, { status: 304, headers });
    return new Response(request.method === 'HEAD' ? null : html, { headers });
  } catch (error) {
    console.error(JSON.stringify({ event: 'paper_template_fallback', reason: error instanceof Error ? error.message : 'unknown' }));
    try { return await legacy(request, env.DATA); } catch { return new Response('Paper temporarily unavailable', { status: 503, headers: { 'Cache-Control': 'no-store' } }); }
  }
}
export default { fetch(request: Request, env: Env): Promise<Response> { return servePaper(request, env); } };
