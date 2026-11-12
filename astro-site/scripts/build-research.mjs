// Render research routes from a private snapshot without modifying main dist.
import { cpSync, existsSync, mkdirSync, readdirSync, renameSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, relative, resolve, isAbsolute } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { researchInputs, planResearchArticles, readResearchCache, restoreResearchArticles, saveResearchCache } from './research-isolated-cache.mjs';

export function prepareResearchWorkspace({ site, snapshot, work }) {
  site = resolve(site); snapshot = resolve(snapshot); work = resolve(work);
  const contains = (parent, child) => {
    const path = relative(parent, child);
    return path === '' || (!path.startsWith('..') && !isAbsolute(path));
  };
  if (contains(work, site) || contains(work, snapshot) || contains(site, work) || contains(snapshot, work))
    throw new Error('Research workspace must be separate from the site and snapshot');
  const buildRoot = resolve(work, 'site');
  const src = resolve(buildRoot, 'src'), pages = resolve(src, 'pages'), data = resolve(src, 'data');
  if (!existsSync(resolve(snapshot, 'build/research/routes.json')) || !existsSync(resolve(snapshot, 'build/research/index.json'))
      || !existsSync(resolve(snapshot, 'public/research/index.json')))
    throw new Error('Research snapshot has no generated routes and index');
  rmSync(work, { recursive: true, force: true });
  cpSync(resolve(site, '../shared'), resolve(work, 'shared'), { recursive: true });
  mkdirSync(buildRoot, { recursive: true });
  for (const name of ['package.json', 'astro.config.mjs', 'postcss.config.mjs', 'tailwind.config.mjs', 'tsconfig.json']) {
    if (existsSync(resolve(site, name))) cpSync(resolve(site, name), resolve(buildRoot, name));
  }
  mkdirSync(pages, { recursive: true });
  mkdirSync(data, { recursive: true });
  mkdirSync(resolve(buildRoot, 'public'), { recursive: true });
  symlinkSync(resolve(site, 'node_modules'), resolve(buildRoot, 'node_modules'), 'dir');
  for (const entry of readdirSync(resolve(site, 'src'), { withFileTypes: true })) {
    if (entry.name === 'pages' || entry.name === 'data') continue;
    // Astro's compiler resolves virtual styles relative to the build root;
    // source symlinks outside that root can break its compile metadata cache.
    cpSync(resolve(site, 'src', entry.name), resolve(src, entry.name), { recursive: true });
  }
  for (const entry of readdirSync(resolve(site, 'src/data'), { withFileTypes: true })) {
    if (entry.name === 'research') continue;
    // Copy small top-level metadata so another release replacing it cannot
    // change the candidate during rendering. Large unrelated trees are not
    // generated or published by this workspace.
    const from = resolve(site, 'src/data', entry.name), to = resolve(data, entry.name);
    if (entry.isFile()) cpSync(from, to);
    else symlinkSync(from, to, 'dir');
  }
  symlinkSync(resolve(snapshot, 'build/research'), resolve(data, 'research'), 'dir');
  for (const path of ['research', 'zh/research', 'fr/research']) {
    const from = resolve(site, 'src/pages', path), to = resolve(pages, path);
    if (existsSync(from)) { mkdirSync(dirname(to), { recursive: true }); cpSync(from, to, { recursive: true }); }
  }
  const graph = resolve(site, 'src/pages/site-data/research-graph.json.ts');
  if (existsSync(graph)) {
    const to = resolve(pages, 'site-data/research-graph.json.ts');
    mkdirSync(dirname(to), { recursive: true }); cpSync(graph, to);
  }
  for (const entry of readdirSync(resolve(site, 'public'), { withFileTypes: true })) {
    if (entry.name === 'site-data' || entry.name === '_headers' || entry.name === '_redirects') continue;
    cpSync(resolve(site, 'public', entry.name), resolve(buildRoot, 'public', entry.name), { recursive: true });
  }
  return { data, buildRoot, output: resolve(buildRoot, 'dist') };
}

export function buildResearch({ site, project, snapshot, destination, work, cacheDir, run = spawnSync }) {
  const started = performance.now();
  if (existsSync(destination)) throw new Error('Research build destination already exists');
  const prepared = prepareResearchWorkspace({ site, snapshot, work });
  // A full isolated candidate must not inherit another release's partial plan.
  const env = { ...process.env, GIDS_RESEARCH_BUILD_DIR: prepared.buildRoot,
    GIDS_SITE_DATA_ROOT: prepared.data, GIDS_RESEARCH_PUBLIC_ROOT: resolve(snapshot, 'public/research'),
    GIDS_STATIC_RENDER: '1', PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN: process.env.GIDS_RESEARCH_STATIC_HOST === '1' ? '0' : '1' };
  delete env.GIDS_RESEARCH_BUILD_PLAN;
  delete env.GIDS_PAGE_BUILD_PLAN;
  delete env.GIDS_REPORTS_BUILD_DIR;
  let inputs, previous, plan;
  if (cacheDir && existsSync(resolve(snapshot, 'article-inputs.json'))) {
    inputs = researchInputs({ site, project, snapshot });
    previous = readResearchCache(cacheDir);
    plan = planResearchArticles(inputs, previous?.manifest);
    if (previous && plan.reusable.length) {
      const planPath = resolve(work, 'article-plan.json');
      writeFileSync(planPath, JSON.stringify({ selected: plan.selected }));
      env.GIDS_RESEARCH_BUILD_PLAN = planPath;
    }
    console.log(JSON.stringify({ event: 'research_article_plan', selected: plan.selected.length, reused: plan.reusable.length }));
  }
  const render = () => {
    const result = run(resolve(site, 'node_modules/.bin/astro'), ['build'], {
      cwd: prepared.buildRoot, stdio: 'inherit', env,
    });
    if (result.error) throw result.error;
    if (result.status !== 0) throw new Error(`Research build failed (${result.status})`);
  };
  render();
  if (env.GIDS_RESEARCH_BUILD_PLAN) {
    try { restoreResearchArticles({ previous, inputs, reusable: plan.reusable, output: prepared.output }); }
    catch (error) {
      // A lost local cache must trigger a complete render, never a partial release.
      console.warn(JSON.stringify({ event: 'research_cache_restore_failed', action: 'full_render', error: String(error) }));
      rmSync(prepared.output, { recursive: true, force: true });
      delete env.GIDS_RESEARCH_BUILD_PLAN;
      plan.reusable = [];
      render();
    }
  }
  const cacheVersion = inputs ? saveResearchCache({ cache: cacheDir, inputs, output: prepared.output }) : null;
  // The sharded public snapshot is a build input only. Browser queries use
  // the rendered research catalogue; do not copy the old data into releases.
  try { renameSync(prepared.output, destination); }
  catch (error) {
    if (error.code !== 'EXDEV') throw error;
    cpSync(prepared.output, destination, { recursive: true, errorOnExist: true, force: false });
  }
  if (inputs) {
    const receipt = `${destination}.inputs.json`, temporary = `${receipt}.${process.pid}.tmp`;
    writeFileSync(temporary, JSON.stringify({ schema: 1, inputs, reused: plan.reusable,
      previous_cache_version: previous?.version ?? null, cache_version: cacheVersion }));
    renameSync(temporary, receipt);
  }
  console.log(JSON.stringify({ event: 'research_build_completed', elapsed_seconds: (performance.now() - started) / 1000,
    destination, snapshot }));
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const site = resolve(import.meta.dirname, '..'), project = resolve(site, '..'), base = resolve(project, 'exports');
  const snapshot = process.env.GIDS_RESEARCH_SNAPSHOT_DIR, output = process.env.GIDS_RESEARCH_BUILD_OUTPUT;
  if (!snapshot || !output) throw new Error('Research snapshot and output paths are required');
  const destination = resolve(output);
  if (dirname(destination) !== base || !destination.startsWith(`${base}/research-stage-`))
    throw new Error('Research output must be a research-stage-* directory under exports');
  const cache = resolve(project, 'data/cache');
  mkdirSync(cache, { recursive: true });
  if (process.env.GIDS_ISOLATED_RESEARCH_LOCKED !== '1') {
    const result = spawnSync('flock', ['-x', resolve(cache, 'research-isolated-build.lock'), process.execPath, fileURLToPath(import.meta.url)], {
      cwd: site, env: { ...process.env, GIDS_ISOLATED_RESEARCH_LOCKED: '1' }, stdio: 'inherit',
    });
    if (result.error) throw result.error;
    process.exit(result.status ?? 1);
  }
  const work = resolve(cache, 'research-isolated-work');
  try { buildResearch({ site, project, snapshot: resolve(snapshot), destination, work,
    cacheDir: resolve(cache, 'isolated-research-build-cache') }); }
  finally { rmSync(work, { recursive: true, force: true }); }
}
