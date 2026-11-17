// Reuse rendered article bundles based on digests recorded during export.
// The cache is an optimization; no cached page is read back for verification.
import { createHash, randomUUID } from 'node:crypto';
import { constants, copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { articlePaths, sharedFingerprint } from './research-build-cache.mjs';
import { createSourceDependencyFingerprinter, sourceFiles } from './source-dependencies.mjs';

const readJson = path => JSON.parse(readFileSync(path, 'utf8'));
const hash = value => createHash('sha256').update(value).digest('hex');

// A template baseline must survive article additions, removals and metadata
// changes. Conservatively cover every static source dependency, including
// dynamic imports, while JSON export membership belongs to the frozen snapshot.
export function researchTemplateFingerprint({ site, project }) {
  const generatedRoot = resolve(site, 'src/data') + '/';
  const publicData = resolve(site, 'public/site-data') + '/';
  const paths = [
    ...sourceFiles(resolve(site, 'src')).filter(path => !(path.startsWith(generatedRoot) && path.endsWith('.json'))),
    ...sourceFiles(resolve(site, 'public')).filter(path => !path.startsWith(publicData)),
    ...sourceFiles(resolve(project, 'shared')),
    // These non-article exports can enter the shared shell/navigation.
    ...['diseases/index.json', 'reports/index.json'].map(path => resolve(site, 'src/data', path)),
    ...['package.json', 'package-lock.json', 'astro.config.mjs', 'tsconfig.json',
      'tailwind.config.mjs', 'postcss.config.mjs', 'scripts/build-research.mjs',
      'scripts/research-isolated-cache.mjs', 'scripts/research-build-cache.mjs',
      'scripts/check-build-assets.mjs', 'scripts/check-seo-output.mjs',
      'scripts/source-dependencies.mjs', 'scripts/public-build-inputs.mjs'].map(path => resolve(site, path)),
  ];
  const digest = createHash('sha256').update('research-template-v1\n');
  for (const path of [...new Set(paths)].sort()) {
    digest.update(relative(project, path) + '\0');
    digest.update(existsSync(path) ? readFileSync(path) : 'missing');
  }
  for (const key of ['GLOBALID_SITE_URL', 'SITE_URL', 'PUBLIC_GIDS_MAINTENANCE_MESSAGE',
    'SITUATION_SHADOW_PREVIEW', 'PUBLIC_GIDS_DATA_MANIFEST_URL', 'PUBLIC_GIDS_SITUATION_MANIFEST_URL',
    'PUBLIC_GIDS_THEME_V1_URL', 'GIDS_PAGES_DATA_WORKER', 'GIDS_EXTERNAL_SURVEILLANCE_DATA',
    'GIDS_RESEARCH_STATIC_HOST', 'PUBLIC_GA4_MEASUREMENT_ID']) {
    digest.update(`${key}=${process.env[key] ?? ''}\n`);
  }
  if (process.env.PUBLIC_GA4_MEASUREMENT_ID === undefined) {
    try { digest.update(JSON.stringify(readJson(resolve(project, 'data/system-settings.json'))?.site?.public_ga4_measurement_id ?? 'G-8P39XV52NC')); }
    catch { digest.update(JSON.stringify('G-8P39XV52NC')); }
  }
  digest.update(String(new Date().getUTCFullYear()));
  return digest.digest('hex');
}

export function researchInputs({ site, project, snapshot }) {
  const receipt = readJson(resolve(snapshot, 'article-inputs.json'));
  const routes = readJson(resolve(snapshot, 'build/research/routes.json'));
  if (receipt?.schema !== 1 || !receipt.articles || !Array.isArray(routes.articles)) throw new Error('Research build inputs are incomplete');
  const articles = {};
  for (const route of routes.articles) {
    const slug = route?.slug, digest = receipt.articles[slug];
    if (typeof slug !== 'string' || !/^[a-z0-9][a-z0-9-]*$/.test(slug) || Object.hasOwn(articles, slug)
        || typeof digest !== 'string' || !/^[a-f0-9]{64}$/.test(digest)) throw new Error('Invalid research build input receipt');
    articles[slug] = { digest, indexable: route.indexable !== false };
  }
  const graph = createSourceDependencyFingerprinter({ siteRoot: site, projectRoot: project });
  const templates = graph(['src/pages/research/articles/[slug].astro', 'src/pages/fr/research/articles/[slug].astro',
    'src/pages/zh/research/articles/[slug].astro', 'src/pages/research/cards/[slug].png.ts']);
  const supporting = ['src/data/diseases/index.json', 'src/data/reports/index.json']
    .map(path => { const file = resolve(site, path); return existsSync(file) ? hash(readFileSync(file)) : ''; });
  const ownCode = hash(readFileSync(import.meta.filename)) + hash(readFileSync(resolve(site, 'scripts/build-research.mjs')));
  return { schema: 1, shared: hash(JSON.stringify([sharedFingerprint(), templates, routes.diseases ?? [], supporting, ownCode,
    process.env.GIDS_RESEARCH_STATIC_HOST === '1' ? 'static-papers' : 'main-research'])), articles };
}

export function planResearchArticles(inputs, previous) {
  const reusable = [], selected = [];
  const shared = previous?.schema === 1 && previous.shared === inputs.shared;
  for (const [slug, article] of Object.entries(inputs.articles)) {
    const old = previous?.articles?.[slug];
    (shared && old?.digest === article.digest && old?.indexable === article.indexable ? reusable : selected).push(slug);
  }
  return { selected, reusable };
}

function copyFile(source, target) {
  mkdirSync(dirname(target), { recursive: true });
  copyFileSync(source, target, constants.COPYFILE_FICLONE);
}

function copyAssets(source, target) {
  if (!existsSync(source)) return;
  mkdirSync(target, { recursive: true });
  for (const item of readdirSync(source, { withFileTypes: true })) {
    const from = resolve(source, item.name), to = resolve(target, item.name);
    if (item.isDirectory()) copyAssets(from, to);
    else if (item.isFile() && !existsSync(to)) copyFile(from, to);
  }
}

export function readResearchCache(cache) {
  try {
    const pointer = readJson(resolve(cache, 'current.json'));
    if (pointer?.schema !== 1 || !/^[a-f0-9-]{36}$/.test(pointer.version)) return null;
    const directory = resolve(cache, 'versions', pointer.version);
    const manifest = readJson(resolve(directory, 'manifest.json'));
    if (manifest?.schema !== 1 || typeof manifest.shared !== 'string' || !manifest.articles) return null;
    return { directory, manifest, version: pointer.version };
  } catch { return null; }
}

export function restoreResearchArticles({ previous, inputs, reusable, output }) {
  for (const slug of reusable) for (const path of articlePaths(slug, inputs.articles[slug].indexable))
    copyFile(resolve(previous.directory, path), resolve(output, path));
  copyAssets(resolve(previous.directory, '_astro'), resolve(output, '_astro'));
}

export function saveResearchCache({ cache, inputs, output }) {
  // The caller serializes builds for this cache; old generations are not in
  // use after their files have been copied into the new candidate.
  const previous = readResearchCache(cache);
  const version = randomUUID(), directory = resolve(cache, 'versions', version);
  mkdirSync(directory, { recursive: true });
  try {
    for (const [slug, article] of Object.entries(inputs.articles))
      for (const path of articlePaths(slug, article.indexable)) copyFile(resolve(output, path), resolve(directory, path));
    copyAssets(resolve(output, '_astro'), resolve(directory, '_astro'));
    writeFileSync(resolve(directory, 'manifest.json'), JSON.stringify(inputs));
    mkdirSync(cache, { recursive: true });
    const pointer = resolve(cache, `.current-${version}.json`);
    writeFileSync(pointer, JSON.stringify({ schema: 1, version }));
    renameSync(pointer, resolve(cache, 'current.json'));
    if (previous) {
      try { rmSync(previous.directory, { recursive: true, force: true }); }
      catch (error) { console.warn(JSON.stringify({ event: 'research_old_cache_cleanup_failed', error: String(error) })); }
    }
    return version;
  } catch (error) {
    rmSync(directory, { recursive: true, force: true });
    throw error;
  }
}
