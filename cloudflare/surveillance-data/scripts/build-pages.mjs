import { build } from 'esbuild';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// Pages Functions uses the double-bracket multipath form for any depth.
const FUNCTION_ROUTES = [
  'site-data/[[path]].js',
  'situation/map-data.json.js',
  'situation/outbreaks/[[path]].js',
];

async function buildScopedFunction(output, route) {
  const destination = resolve(output, 'functions', route);
  mkdirSync(resolve(destination, '..'), { recursive: true });
  const workerSource = resolve(import.meta.dirname, '../src/index.ts');
  await build({
    stdin: {
      contents: `import worker, { dataRoute } from ${JSON.stringify(workerSource)};
export async function onRequest(context) {
  if (!dataRoute(new URL(context.request.url).pathname)) return context.next();
  return worker.fetch(context.request, context.env, context);
}
`,
      resolveDir: resolve(import.meta.dirname, '..'),
      sourcefile: 'pages-data-function.js',
    },
    outfile: destination,
    bundle: true,
    minify: true,
    format: 'esm',
    platform: 'browser',
    target: 'es2022',
  });
}

export async function buildPages(directory) {
  const output = resolve(directory);
  if (!existsSync(output)) throw new Error('Build the static site before packaging its Pages data Worker');
  // Pages Functions are the only supported deployment mode. Remove stale
  // worker output so a reused build cannot take control of every URL.
  rmSync(resolve(output, '_worker.js'), { force: true });
  rmSync(resolve(output, 'functions'), { recursive: true, force: true });
  for (const route of FUNCTION_ROUTES) await buildScopedFunction(output, route);
  writeFileSync(resolve(output, '_routes.json'), JSON.stringify({
    version: 1,
    include: ['/site-data/*', '/situation/*'],
    exclude: [],
  }));
  console.log('[pages-data-worker] packaged path-scoped Pages Functions for /site-data/* and Situation data aliases');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await buildPages(process.argv[2] || resolve(import.meta.dirname, '../../../astro-site/dist'));
}
