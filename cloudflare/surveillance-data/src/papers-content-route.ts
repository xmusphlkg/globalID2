/** Resolve an immutable research generation through its small sharded catalogue. */
import { templateArticleResponse } from './paper-template-content.ts';
import { isResearchPagePath, isResearchResourcePath, researchPageUrl } from '../../../shared/research-urls.ts';
const generationPath = /^\/research-publication\/([a-f0-9]{64})\/((?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]*\/?$)/;

export function papersContentPath(pathname: string): { generation: string; key: string } | null {
  const match = generationPath.exec(pathname);
  if (!match || !match[2] || match[2].split('/').some(part => part === '.' || part === '..')) return null;
  return { generation: match[1], key: match[2] };
}

/** Stable public URLs resolve through the current pointer without changing it. */
export function researchCurrentPath(pathname: string): string | null {
  if (!/^\/(?:(?:zh\/|fr\/)?research(?:\/|$)|site-data\/research\/)/.test(pathname)
      || !/^\/(?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]*\/?$/.test(pathname)
      || pathname.split('/').some(part => part === '.' || part === '..')) return null;
  let key = pathname.slice(1);
  if (!key.endsWith('/') && !key.split('/').at(-1)?.includes('.')) key += '/';
  return key;
}

function error(status: number) {
  return new Response(JSON.stringify({ error: status === 404 ? 'not_found' : 'data_unavailable' }), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

async function catalogueJson<T>(request: Request, env: Env, key: string,
                                 ctx?: ExecutionContext): Promise<T | null> {
  // Immutable catalogue metadata is reused at the edge; pointers are never
  // cached. Cache misses and cache eviction are harmless R2 reads.
  const cache = typeof caches === 'undefined' ? undefined : caches.default;
  const cacheUrl = new URL(request.url);
  cacheUrl.pathname = `/.research-catalogue-cache/${key}`;
  cacheUrl.search = '';
  const cacheKey = new Request(cacheUrl.toString());
  let cached: Response | undefined;
  try { cached = await cache?.match(cacheKey); }
  catch (error) { console.error(JSON.stringify({ event: 'research_catalogue_cache_read_failed', error_type: error instanceof Error ? error.name : 'Error' })); }
  if (cached) return cached.json<T>();
  const object = await env.DATA.get(key);
  if (!object) return null;
  if (object.size > 2 * 1024 * 1024) throw new Error('Research catalogue too large');
  const text = await object.text();
  const value = JSON.parse(text) as T;
  if (cache) {
    const stored = new Response(text, { headers: { 'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=31536000, immutable' } });
    const write = cache.put(cacheKey, stored).catch(error => {
      console.error(JSON.stringify({ event: 'research_catalogue_cache_failed', error_type: error instanceof Error ? error.name : 'Error' }));
    });
    if (ctx) ctx.waitUntil(write); else await write;
  }
  return value;
}

/** Public navigation and SEO agree; only supporting resources use a generation. */
function rewrittenPath(value: string, generation: string, publicBase: string): string | null {
  let url: URL;
  try { url = new URL(value, 'https://globalinfectiousdisease.com'); } catch { return null; }
  if (!['https:', 'http:'].includes(url.protocol)
      || !['globalinfectiousdisease.com', 'www.globalinfectiousdisease.com', 'papers.globalinfectiousdisease.com'].includes(url.hostname)) return null;
  if (isResearchPagePath(url.pathname)) return researchPageUrl(`${url.pathname}${url.search}${url.hash}`);
  if (!isResearchResourcePath(url.pathname)) return null;
  return `${publicBase}/research-publication/${generation}${url.pathname}${url.search}${url.hash}`;
}

function rewriteHtml(response: Response, generation: string, publicBase: string): Response {
  if (typeof HTMLRewriter === 'undefined') return response;
  return new HTMLRewriter().on('head', {
    element(element) { element.prepend(`<meta name="gids-research-generation" content="${generation}">`, { html: true }); },
  }).on('[href], [src], [data-graph-src], [component-url], [renderer-url], meta[property="og:url"]', {
    element(element) {
      for (const attribute of ['href', 'src', 'data-graph-src', 'component-url', 'renderer-url', ...(element.tagName === 'meta' ? ['content'] : [])]) {
        const value = element.getAttribute(attribute);
        const next = value && rewrittenPath(value, generation, publicBase);
        if (next) element.setAttribute(attribute, next);
      }
    },
  }).transform(response);
}

export async function serveCurrentResearch(request: Request, env: Env, key: string, ctx?: ExecutionContext): Promise<Response> {
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { Allow: 'GET, HEAD, OPTIONS' } });
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response(null, { status: 405, headers: { Allow: 'GET, HEAD, OPTIONS' } });
  try {
    const pointer = await env.DATA.get('site-data/research-publication/current.json');
    if (!pointer) return error(404);
    if (pointer.size > 2 * 1024 * 1024) throw new Error('Research pointer too large');
    const current = await pointer.json<{ schema_version: number; release_id?: string; papers?: { papers_generation_sha256?: string } }>();
    const generation = current.papers?.papers_generation_sha256;
    if (current.schema_version !== 1 || typeof current.release_id !== 'string' || !/^[A-Za-z0-9_.:-]{1,160}$/.test(current.release_id)
        || typeof generation !== 'string' || !/^[a-f0-9]{64}$/.test(generation))
      throw new Error('Research pointer has no papers generation');
    const requestHeaders = new Headers(request.headers);
    requestHeaders.delete('If-None-Match');
    const response = await servePapersContent(new Request(request, { headers: requestHeaders }), env, { generation, key }, ctx);
    const headers = new Headers(response.headers);
    headers.set('Cache-Control', 'no-store');
    headers.set('X-GIDS-Research-Release', current.release_id);
    headers.delete('ETag');
    return new Response(response.body, { status: response.status, headers });
  } catch (cause) {
    console.error(JSON.stringify({ event: 'research_pointer_read_failed', error_type: cause instanceof Error ? cause.name : 'Error' }));
    return error(503);
  }
}

export async function servePapersContent(request: Request, env: Env, route: { generation: string; key: string }, ctx?: ExecutionContext): Promise<Response> {
  // Removed snapshot page URLs must never fall through to static Pages or R2.
  // The stable route calls this resolver internally with its original short URL.
  if (papersContentPath(new URL(request.url).pathname) && !isResearchResourcePath(`/${route.key}`)) {
    return new Response(request.method === 'HEAD' ? null : 'Gone', { status: 410,
      headers: { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex' } });
  }
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: { Allow: 'GET, HEAD, OPTIONS' } });
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response(null, { status: 405, headers: { Allow: 'GET, HEAD, OPTIONS' } });
  try {
    const catalogue = await catalogueJson<{ schema_version: number; generation_sha256: string; shards: Record<string, { key: string }> }>(
      request, env, `releases/sha256/catalogues/${route.generation}.json`, ctx);
    if (!catalogue) return error(404);
    if (catalogue.schema_version !== 2 || catalogue.generation_sha256 !== route.generation || !catalogue.shards) throw new Error('Invalid research catalogue');
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(route.key));
    const group = [...new Uint8Array(bytes)].map(value => value.toString(16).padStart(2, '0')).join('').slice(0, 2);
    const shardKey = catalogue.shards[group]?.key;
    if (!shardKey) return error(404);
    if (!/^releases\/sha256\/catalogue-shards\/[a-f0-9]{64}\.json$/.test(shardKey)) throw new Error('Invalid research catalogue shard');
    const data = await catalogueJson<{ schema_version: number; items: Array<{ logical_key: string; key: string; content_type: string; paper_template?: unknown; [key: string]: unknown }> }>(request, env, shardKey, ctx);
    if (!data) throw new Error('Research catalogue shard unavailable');
    if (data.schema_version !== 1 || !Array.isArray(data.items)) throw new Error('Invalid research catalogue shard');
    const entry = data.items.find(item => item.logical_key === route.key);
    if (!entry) return error(404);
    if ('paper_template' in entry) {
      const response = await templateArticleResponse(request, env, entry, route.key, route.generation, ctx);
      const publicBase = new URL(request.url).hostname === 'papers.globalinfectiousdisease.com' ? 'https://globalinfectiousdisease.com' : '';
      return response.body ? rewriteHtml(response, route.generation, publicBase) : response;
    }
    if (!/^releases\/sha256\/objects\/[a-f0-9]{64}\/[a-f0-9]{16}$/.test(entry.key) || typeof entry.content_type !== 'string') throw new Error('Invalid research object');
    let object: R2Object | null;
    let body: ReadableStream | null = null;
    if (request.method === 'HEAD') object = await env.DATA.head(entry.key);
    else {
      const result = await env.DATA.get(entry.key);
      object = result;
      body = result?.body ?? null;
    }
    if (!object) throw new Error('Research object unavailable');
    const responseHeaders = new Headers({ 'Content-Type': entry.content_type, 'Content-Length': String(object.size),
      'Cache-Control': 'public, max-age=31536000, immutable', ETag: object.httpEtag,
      'X-Content-Type-Options': 'nosniff', 'Access-Control-Allow-Origin': '*' });
    const html = /^text\/html(?:;|$)/i.test(entry.content_type);
    const css = /^text\/css(?:;|$)/i.test(entry.content_type);
    const encoding = object.httpMetadata?.contentEncoding;
    if (encoding && encoding !== 'gzip') throw new Error('Unsupported research object encoding');
    if (html || css || encoding) responseHeaders.delete('Content-Length');
    if (request.headers.get('If-None-Match')?.split(',').map(value => value.trim()).includes(object.httpEtag)) {
      if (body) await body.cancel();
      responseHeaders.delete('Content-Length');
      return new Response(null, { status: 304, headers: responseHeaders });
    }
    if (request.method === 'HEAD') return new Response(null, { headers: responseHeaders });
    // R2 bindings expose stored bytes, unlike fetch's decoded response body.
    // Decode before HTMLRewriter/CSS rewriting and let the response negotiate
    // its own HTTP encoding. Otherwise gzip bytes are served as HTML/JSON.
    if (body && encoding === 'gzip') body = body.pipeThrough(new DecompressionStream('gzip'));
    if (css && body) {
      if (object.size > 4 * 1024 * 1024) throw new Error('Research stylesheet too large');
      let decodedBytes = 0;
      const bounded = body.pipeThrough(new TransformStream({
        transform(chunk, controller) {
          decodedBytes += chunk.byteLength;
          if (decodedBytes > 4 * 1024 * 1024) throw new Error('Research stylesheet too large');
          controller.enqueue(chunk);
        },
      }));
      const stylesheet = await new Response(bounded).text();
      return new Response(stylesheet.replaceAll('/_astro/', `/research-publication/${route.generation}/_astro/`), { headers: responseHeaders });
    }
    const response = new Response(body, { headers: responseHeaders });
    const publicBase = new URL(request.url).hostname === 'papers.globalinfectiousdisease.com' ? 'https://globalinfectiousdisease.com' : '';
    return html ? rewriteHtml(response, route.generation, publicBase) : response;
  } catch (cause) {
    console.error(JSON.stringify({ event: 'research_papers_read_failed', generation: route.generation,
      error_type: cause instanceof Error ? cause.name : 'Error' }));
    return error(503);
  }
}
