// Private build inputs keep live surveillance out of a template publication.
// Symlinks avoid copying the retained Research/Reports payload into staging;
// Vite follows source symlinks while copying public assets to dist.
import { existsSync, mkdirSync, readdirSync, rmSync, statSync, symlinkSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';

export function isSurveillancePublicPath(path) {
  const value = path.split(sep).join('/').replace(/^\.\//, '');
  return /^site-data\/(?:countries|diseases|objects)(?:\/|$)/.test(value)
    || /^site-data\/(?:current|meta|site-status)\.json$/.test(value);
}

export function preparePublicBuildInputs({ publicRoot, stageRoot, external = false, independentResearch = process.env.GIDS_RESEARCH_PUBLICATION_ENABLED === '1' }) {
  publicRoot = resolve(publicRoot);
  if (!external) return { publicDir: publicRoot, staged: false, cleanup() {} };
  stageRoot = resolve(stageRoot);
  if (stageRoot === publicRoot || stageRoot.startsWith(publicRoot + sep) || publicRoot.startsWith(stageRoot + sep)) throw new Error('Public staging must be separate from source inputs');
  rmSync(stageRoot, { recursive: true, force: true });
  mkdirSync(stageRoot, { recursive: true });
  const link = (source, target) => {
    const key = relative(publicRoot, source);
    if (isSurveillancePublicPath(key)) return;
    if (independentResearch && /^site-data\/research(?:\/|$)/.test(key.split(sep).join('/'))) return;
    if (key === 'site-data') {
      mkdirSync(target, { recursive: true });
      for (const name of readdirSync(source)) link(resolve(source, name), resolve(target, name));
      return;
    }
    mkdirSync(dirname(target), { recursive: true });
    symlinkSync(source, target, statSync(source).isDirectory() ? 'junction' : 'file');
  };
  if (existsSync(publicRoot)) for (const name of readdirSync(publicRoot)) link(resolve(publicRoot, name), resolve(stageRoot, name));
  return { publicDir: stageRoot, staged: true, cleanup() { rmSync(stageRoot, { recursive: true, force: true }); } };
}
