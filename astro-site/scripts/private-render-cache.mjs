import { existsSync, readFileSync, realpathSync, rmSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';

// A frozen template can run under an older worker that prepopulated its
// private caches. Identify the workspace using the worker's existing receipt.
export function privateFullRender(root, env = process.env) {
  if (env.GIDS_FORCE_FULL_RENDER !== '1'
    || basename(dirname(root)) !== 'code-workspaces'
    || basename(dirname(dirname(root))) !== 'exports') return false;
  try {
    const receipt = JSON.parse(readFileSync(resolve(root, 'workspace.json'), 'utf8'));
    return receipt.schema === 1 && receipt.task_uuid === basename(root);
  } catch { return false; }
}

// Called under the private build lock. Only these renderer outputs were
// copied as warm-up hints; other files include live locks and runtime state.
export function discardImportedRenderCache(root) {
  const cache = resolve(root, 'data/cache');
  if (!existsSync(cache) || realpathSync(cache) !== cache) return;
  for (const name of ['site-pages', 'site-page-manifest.json', 'research-pages',
    'research-build-manifest.json', 'astro-assets']) {
    rmSync(resolve(cache, name), { recursive: true, force: true });
  }
}
