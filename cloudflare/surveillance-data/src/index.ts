/** Same-origin, streaming JSON reads; publication never builds this Worker. */
export function objectPath(pathname: string): string | null {
  if (pathname === '/site-data/current.json') return 'current.json';
  const match = /^\/site-data\/(objects\/[a-f0-9]{64}\.json)$/.exec(pathname);
  if (match) return match[1];
  const logical = /^\/site-data\/((?:countries|diseases)\/[a-z0-9-]+\.json|meta\.json|site-status\.json)$/.exec(pathname);
  return logical?.[1] ?? null;
}

export function dataRoute(pathname: string) {
  const updates = /^\/site-data\/updates\/(latest\.json|\d{4}-(?:0[1-9]|1[0-2])\.json)$/.exec(pathname);
  if (updates) return { channel: 'updates' as const, relative: updates[1], publicRoot: '/site-data/updates/' };
  const publicRoot = '/site-data/situation/';
  const relative = pathname.startsWith(publicRoot) ? pathname.slice(publicRoot.length)
    : pathname === '/situation/map-data.json' ? 'map-data.json'
    : /^\/situation\/outbreaks\/[A-Za-z0-9_-]+\.json$/.test(pathname) ? pathname.slice('/situation/'.length) : null;
  if (relative && (/^(?:current\.json|objects\/[a-f0-9]{64}\.json)$/.test(relative)
    || (/^(?:[A-Za-z0-9_-]+\/)*[A-Za-z0-9_-]+\.json$/.test(relative) && !relative.startsWith('objects/')))) {
    return { channel: 'situation' as const, relative, publicRoot };
  }
  const surveillance = objectPath(pathname);
  return surveillance ? { channel: 'surveillance' as const, relative: surveillance, publicRoot: '/site-data/' } : null;
}

function headers(immutable = false): Headers {
  return new Headers({
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-store',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Expose-Headers': 'ETag, Content-Length',
    'X-Content-Type-Options': 'nosniff',
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const route = dataRoute(new URL(request.url).pathname);
    if (!route) return new Response('{"error":"not_found"}', { status: 404, headers: headers() });
    const { relative, channel, publicRoot } = route;
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: headers() });
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      const responseHeaders = headers();
      responseHeaders.set('Allow', 'GET, HEAD, OPTIONS');
      return new Response('{"error":"method_not_allowed"}', { status: 405, headers: responseHeaders });
    }
    const prefix = (channel === 'updates' ? 'site-data/updates' : channel === 'situation' ? env.SITUATION_PREFIX || 'site-data/situation' : env.DATA_PREFIX || 'site-data/surveillance').replace(/^\/+|\/+$/g, '');
    const key = `${prefix}/${relative}`;
    try {
      if (channel !== 'updates' && relative !== 'current.json' && !relative.startsWith('objects/')) {
        const pointer = await env.DATA.get(`${prefix}/current.json`);
        if (!pointer) return new Response('{"error":"not_found"}', { status: 404, headers: headers() });
        if (pointer.size > 2 * 1024 * 1024) throw new Error('Data manifest exceeds supported size');
        const manifest = await pointer.json<{ schema_version?: number; files?: Record<string, { url?: string; sha256?: string }> }>();
        const entry = manifest.files?.[relative];
        if (manifest.schema_version !== 1 || !entry) return new Response('{"error":"not_found"}', { status: 404, headers: headers() });
        const target = /^objects\/([a-f0-9]{64})\.json$/.exec(entry.url ?? '');
        if (!target || target[1] !== entry.sha256) throw new Error('Invalid immutable data reference');
        const responseHeaders = headers();
        responseHeaders.set('Location', `${publicRoot}${target[0]}`);
        // Legacy logical dataset URLs always resolve through the latest pointer.
        // Snapshot-aware browsers resolve these once in their own fixed manifest.
        return new Response(null, { status: 307, headers: responseHeaders });
      }
      let object: R2Object | null;
      let body: ReadableStream | null = null;
      if (request.method === 'HEAD') object = await env.DATA.head(key);
      else {
        const result = await env.DATA.get(key);
        object = result;
        body = result?.body ?? null;
      }
      // Missing pointers must not be negatively cached: the first initialized
      // generation may arrive independently of the template deployment.
      if (!object) return new Response('{"error":"not_found"}', { status: 404, headers: headers() });
      const responseHeaders = headers(channel !== 'updates' && relative !== 'current.json');
      responseHeaders.set('ETag', object.httpEtag);
      responseHeaders.set('Content-Length', String(object.size));
      if (object.httpMetadata?.contentEncoding) responseHeaders.set('Content-Encoding', object.httpMetadata.contentEncoding);
      if (request.headers.get('If-None-Match')?.split(',').map(value => value.trim()).includes(object.httpEtag)) {
        if (body) await body.cancel();
        responseHeaders.delete('Content-Length');
        return new Response(null, { status: 304, headers: responseHeaders });
      }
      // R2 bodies are streams, including multi-megabyte country datasets.
      return new Response(body, { headers: responseHeaders,
        encodeBody: object.httpMetadata?.contentEncoding ? 'manual' : 'automatic' });
    } catch (error) {
      console.error(JSON.stringify({ event: 'data_read_failed', channel, path: relative,
        error_type: error instanceof Error ? error.name : 'Error' }));
      return new Response('{"error":"data_unavailable"}', { status: 503, headers: headers() });
    }
  },
} satisfies ExportedHandler<Env>;
