/** Shared data handler used by the path-scoped Pages Functions. */
import worker, { dataRoute } from './index.ts';

type PagesBindings = Parameters<PagesFunction<Env>>[0]['env'];

export default {
  async fetch(request: Request, env: PagesBindings, ctx?: ExecutionContext): Promise<Response> {
    if (dataRoute(new URL(request.url).pathname)) return worker.fetch(request, env, ctx);
    return env.ASSETS.fetch(request);
  },
} satisfies ExportedHandler<PagesBindings>;
