import { build } from 'esbuild';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export async function buildPages(directory) {
  const output = resolve(directory);
  if (!existsSync(output)) throw new Error('Build the static site before packaging its Pages data Worker');
  await build({ entryPoints: [resolve(import.meta.dirname, '../src/pages.ts')],
    outfile: resolve(output, '_worker.js'), bundle: true, minify: true, format: 'esm', platform: 'browser', target: 'es2022' });
  writeFileSync(resolve(output, '_routes.json'), readFileSync(resolve(import.meta.dirname, '../src/pages-routes.json')));
  console.log('[pages-data-worker] packaged same-origin JSON routes and static asset fallback');
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  await buildPages(process.argv[2] || resolve(import.meta.dirname, '../../../astro-site/dist'));
}
