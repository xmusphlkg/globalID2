/** Research-only routing uses the same atomic publication as literature jobs. */
import { papersContentPath, researchCurrentPath, serveCurrentResearch, servePapersContent } from '../../surveillance-data/src/papers-content-route.ts';

export async function serveResearch(request: Request, env: Env, ctx?: ExecutionContext): Promise<Response> {
  const path = new URL(request.url).pathname;
  const versioned = papersContentPath(path);
  if (versioned) return servePapersContent(request, env, versioned, ctx);
  const current = researchCurrentPath(path);
  if (current) return serveCurrentResearch(request, env, current, ctx);
  return new Response('Not found', { status: 404, headers: { 'Cache-Control': 'no-store' } });
}

export default { fetch: serveResearch } satisfies ExportedHandler<Env>;
