import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileHash, fileFingerprints, gitSourceIdentities, sourceIdentity } from './file-fingerprints.mjs';
import { createSourceDependencyFingerprinter } from './source-dependencies.mjs';

export const siteRoot = resolve(import.meta.dirname, '..');
export const projectRoot = resolve(siteRoot, '..');
export const distRoot = resolve(siteRoot, 'dist');
export const cacheRoot = resolve(projectRoot, 'data', 'cache');
export const manifestPath = resolve(cacheRoot, 'research-build-manifest.json');
export const pendingPath = resolve(cacheRoot, 'research-build-pending.json');
export const planPath = resolve(cacheRoot, 'research-build-plan.json');
// Rendered article bundles are kept here between releases. The release
// pipeline moves them out of dist (they are published from the papers host),
// so dist can no longer serve as the reuse source for incremental builds.
export const pageCacheRoot = resolve(cacheRoot, 'research-pages');
export const astroAssetCacheRoot = resolve(cacheRoot, 'astro-assets');
// This server-only route is imported only by its localized report routes.
// Its content hash belongs to those bundles, not every paper in the catalogue.
export const reportDiseaseTemplate = resolve(siteRoot, 'src/pages/countries/[country]/reports/[id]/[disease].astro');
const defaultMeasurementId = 'G-8P39XV52NC';

const sha = value => createHash('sha256').update(value).digest('hex');
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

function walk(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : entry.isFile() ? [path] : [];
  });
}

export function mergeAstroAssetTree(sourceRoot, destinationRoot) {
  if (!existsSync(sourceRoot)) return 0;
  mkdirSync(destinationRoot, { recursive: true });
  let copied = 0;
  for (const entry of readdirSync(sourceRoot, { withFileTypes: true })) {
    const source = join(sourceRoot, entry.name);
    const destination = join(destinationRoot, entry.name);
    if (entry.isDirectory()) {
      copied += mergeAstroAssetTree(source, destination);
    } else if (entry.isFile() && !existsSync(destination)) {
      mkdirSync(dirname(destination), { recursive: true });
      copyFileSync(source, destination);
      copied += 1;
    }
  }
  return copied;
}

export function replaceAstroAssetCache(sourceRoot, destinationRoot) {
  rmSync(destinationRoot, { recursive: true, force: true });
  return mergeAstroAssetTree(sourceRoot, destinationRoot);
}

function effectiveMeasurementId() {
  if (process.env.PUBLIC_GA4_MEASUREMENT_ID !== undefined) return process.env.PUBLIC_GA4_MEASUREMENT_ID.trim().toUpperCase();
  try {
    const settings = readJson(resolve(projectRoot, 'data', 'system-settings.json'));
    if (Object.prototype.hasOwnProperty.call(settings?.site ?? {}, 'public_ga4_measurement_id')) {
      return String(settings.site.public_ga4_measurement_id ?? '').trim().toUpperCase();
    }
  } catch { /* fall back to the site default */ }
  return defaultMeasurementId;
}

export function sharedFingerprint() {
  const files = [
    ...walk(resolve(siteRoot, 'src/styles')),
    ...['middleware.ts', 'middleware.js', 'env.d.ts', 'content.config.ts'].map(name => resolve(siteRoot, 'src', name)),
    ...walk(resolve(siteRoot, 'public')).filter(path => !path.startsWith(resolve(siteRoot, 'public', 'site-data') + '/')),
    resolve(siteRoot, 'package.json'),
    resolve(siteRoot, 'package-lock.json'),
    resolve(siteRoot, 'astro.config.mjs'),
    resolve(siteRoot, 'tsconfig.json'),
    ...readdirSync(siteRoot).filter(name => /(?:tailwind|postcss).*config/.test(name)).map(name => resolve(siteRoot, name)),
    resolve(siteRoot, 'scripts', 'build-site.mjs'),
    resolve(siteRoot, 'scripts', 'research-build-cache.mjs'),
    resolve(siteRoot, 'scripts', 'file-fingerprints.mjs'),
    resolve(siteRoot, 'scripts', 'source-dependencies.mjs'),
  ].filter(existsSync).sort();
  const digest = createHash('sha256');
  const identities = gitSourceIdentities(projectRoot);
  for (const path of files) {
    digest.update(relative(siteRoot, path));
    digest.update(sourceIdentity(path, identities, fileFingerprints));
  }
  const graph = createSourceDependencyFingerprinter({ siteRoot, projectRoot });
  digest.update(graph(['astro.config.mjs', 'src/middleware.ts', 'src/middleware.js']));
  digest.update(String(existsSync(resolve(siteRoot, 'src/data/situation/international/latest.json'))));
  digest.update(String(existsSync(resolve(siteRoot, 'src/data/situation/international/map.json'))));
  for (const key of ['GLOBALID_SITE_URL', 'SITE_URL', 'PUBLIC_GIDS_MAINTENANCE_MESSAGE', 'SITUATION_SHADOW_PREVIEW']) {
    digest.update(`${key}=${process.env[key] ?? ''}\n`);
  }
  digest.update(`PUBLIC_GA4_MEASUREMENT_ID=${effectiveMeasurementId()}\n`);
  digest.update(String(new Date().getUTCFullYear()));
  return digest.digest('hex');
}

export function articlePaths(slug, indexable) {
  const paths = [
    `research/articles/${slug}/index.html`,
    `fr/research/articles/${slug}/index.html`,
    `zh/research/articles/${slug}/index.html`,
  ];
  if (indexable) paths.push(`research/cards/${slug}.png`);
  return paths;
}

export function sourceSnapshot() {
  const routes = readJson(resolve(siteRoot, 'src/data/research/routes.json'));
  const articles = {};
  for (const route of routes.articles ?? []) {
    const slug = String(route.slug ?? '');
    if (!/^[a-z0-9][a-z0-9-]*$/.test(slug) || articles[slug]) {
      throw new Error(`Invalid or duplicate research slug: ${slug}`);
    }
    const detail = resolve(siteRoot, 'src/data/research/articles', `${slug}.json`);
    articles[slug] = {
      fingerprint: sha(`${fileHash(detail)}:${route.indexable !== false}`),
      paths: articlePaths(slug, route.indexable !== false),
    };
  }
  const graph = createSourceDependencyFingerprinter({ siteRoot, projectRoot });
  const templates = graph([
    'src/pages/research/articles/[slug].astro',
    'src/pages/fr/research/articles/[slug].astro',
    'src/pages/zh/research/articles/[slug].astro',
    'src/pages/research/cards/[slug].png.ts',
  ]);
  return { schema: 1, shared: sha(`${sharedFingerprint()}:${templates}:${JSON.stringify(routes.diseases ?? [])}`), articles };
}

function readPreviousManifest() {
  try {
    const value = readJson(manifestPath);
    return value?.schema === 1 ? value : null;
  } catch { return null; }
}

export function planResearchBuild({ incremental = false, snapshot = sourceSnapshot(), previous = readPreviousManifest(), outputRoot = pageCacheRoot } = {}) {
  const selected = [];
  const reused = [];
  const full = !incremental || !previous || previous.shared !== snapshot.shared;
  for (const [slug, current] of Object.entries(snapshot.articles)) {
    const old = previous?.articles?.[slug];
    const available = !full && old?.fingerprint === current.fingerprint
      && current.paths.every(path => old.files?.[path] && existsSync(resolve(outputRoot, path))
        && fileHash(resolve(outputRoot, path)) === old.files[path]);
    (available ? reused : selected).push(slug);
  }
  return { mode: full ? 'full' : 'incremental', snapshot, selected, reused };
}

export function restoreReusablePages(plan, source = pageCacheRoot, target = distRoot) {
  for (const slug of plan.reused) {
    for (const path of plan.snapshot.articles[slug].paths) {
      const destination = resolve(target, path);
      mkdirSync(dirname(destination), { recursive: true });
      fileFingerprints.copy(resolve(source, path), destination);
    }
  }
}

export function reusedAssetReferencesAvailable(plan, outputRoot = distRoot) {
  for (const slug of plan.reused) {
    for (const path of plan.snapshot.articles[slug].paths.filter(path => path.endsWith('.html'))) {
      const html = readFileSync(resolve(outputRoot, path), 'utf8');
      for (const match of html.matchAll(/\/_astro\/([A-Za-z0-9._/-]+)/g)) {
        if (!existsSync(resolve(outputRoot, '_astro', match[1]))) return false;
      }
    }
  }
  return true;
}

export function writePending(plan) {
  mkdirSync(cacheRoot, { recursive: true });
  writeFileSync(pendingPath, JSON.stringify(plan.snapshot));
}

export function finalizeResearchSnapshot(snapshot, {
  outputRoot = distRoot,
  targetManifest = manifestPath,
  sourcePending = pendingPath,
  pageCache = pageCacheRoot,
  label = 'research-cache',
} = {}) {
  if (snapshot?.schema !== 1 || !snapshot.articles || typeof snapshot.articles !== 'object') {
    throw new Error('Invalid research build snapshot');
  }
  // build-site.mjs writes this snapshot only after verifying inputs stayed
  // unchanged for the whole render. Sources may advance after its lock is
  // released, so record the exact snapshot represented by the built pages.
  for (const article of Object.values(snapshot.articles)) {
    article.files = {};
    for (const path of article.paths) {
      const output = resolve(outputRoot, path);
      if (!existsSync(output) || !statSync(output).isFile()) throw new Error(`Missing research output: ${path}`);
      article.files[path] = fileHash(output);
    }
  }
  // Keep the rendered bundles so the next incremental build can reuse them
  // after the release step has moved them out of dist, and drop bundles that
  // no longer belong to any article.
  const keep = new Set();
  for (const article of Object.values(snapshot.articles)) {
    for (const path of article.paths) {
      const target = resolve(pageCache, path);
      mkdirSync(dirname(target), { recursive: true });
      fileFingerprints.copy(resolve(outputRoot, path), target);
      keep.add(target);
    }
  }
  for (const stale of walk(pageCache)) if (!keep.has(stale)) rmSync(stale, { force: true });
  const temporary = `${targetManifest}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify(snapshot));
  renameSync(temporary, targetManifest);
  rmSync(sourcePending, { force: true });
  process.stdout.write(`[${label}] recorded ${Object.keys(snapshot.articles).length} complete page bundles\n`);
}

export function finalizeResearchBuild() {
  finalizeResearchSnapshot(readJson(pendingPath));
}
