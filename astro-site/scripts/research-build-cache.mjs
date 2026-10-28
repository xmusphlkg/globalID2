import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';

export const siteRoot = resolve(import.meta.dirname, '..');
export const projectRoot = resolve(siteRoot, '..');
export const distRoot = resolve(siteRoot, 'dist');
export const cacheRoot = resolve(projectRoot, 'data', 'cache');
export const manifestPath = resolve(cacheRoot, 'research-build-manifest.json');
export const pendingPath = resolve(cacheRoot, 'research-build-pending.json');
export const planPath = resolve(cacheRoot, 'research-build-plan.json');
const defaultMeasurementId = 'G-8P39XV52NC';

const sha = value => createHash('sha256').update(value).digest('hex');
const fileHash = path => sha(readFileSync(path));
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

function walk(directory) {
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path) : entry.isFile() ? [path] : [];
  });
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
    ...walk(resolve(siteRoot, 'src')).filter(path => !path.startsWith(resolve(siteRoot, 'src', 'data') + '/')),
    ...walk(resolve(siteRoot, 'public')).filter(path => !path.startsWith(resolve(siteRoot, 'public', 'site-data') + '/')),
    resolve(siteRoot, 'package.json'),
    resolve(siteRoot, 'package-lock.json'),
    resolve(siteRoot, 'astro.config.mjs'),
    resolve(siteRoot, 'scripts', 'build-site.mjs'),
    resolve(siteRoot, 'scripts', 'research-build-cache.mjs'),
  ].filter(existsSync).sort();
  const routes = readJson(resolve(siteRoot, 'src/data/research/routes.json'));
  const digest = createHash('sha256');
  for (const path of files) {
    digest.update(relative(siteRoot, path));
    digest.update(fileHash(path));
  }
  digest.update(JSON.stringify(routes.diseases ?? []));
  digest.update(String(existsSync(resolve(siteRoot, 'src/data/situation/international/latest.json'))));
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
  return { schema: 1, shared: sharedFingerprint(), articles };
}

function readPreviousManifest() {
  try {
    const value = readJson(manifestPath);
    return value?.schema === 1 ? value : null;
  } catch { return null; }
}

export function planResearchBuild({ incremental = false, snapshot = sourceSnapshot(), previous = readPreviousManifest(), outputRoot = distRoot } = {}) {
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

export function stageReusablePages(plan) {
  const stage = resolve(cacheRoot, `research-build-stage-${process.pid}`);
  rmSync(stage, { recursive: true, force: true });
  for (const slug of plan.reused) {
    for (const path of plan.snapshot.articles[slug].paths) {
      const target = resolve(stage, path);
      mkdirSync(dirname(target), { recursive: true });
      copyFileSync(resolve(distRoot, path), target);
    }
  }
  return stage;
}

export function restoreReusablePages(plan, stage) {
  for (const slug of plan.reused) {
    for (const path of plan.snapshot.articles[slug].paths) {
      const target = resolve(distRoot, path);
      mkdirSync(dirname(target), { recursive: true });
      copyFileSync(resolve(stage, path), target);
    }
  }
}

export function reusedAssetReferencesAvailable(plan) {
  for (const slug of plan.reused) {
    for (const path of plan.snapshot.articles[slug].paths.filter(path => path.endsWith('.html'))) {
      const html = readFileSync(resolve(distRoot, path), 'utf8');
      for (const match of html.matchAll(/\/_astro\/([A-Za-z0-9._/-]+)/g)) {
        if (!existsSync(resolve(distRoot, '_astro', match[1]))) return false;
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
  const temporary = `${targetManifest}.${process.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify(snapshot));
  renameSync(temporary, targetManifest);
  rmSync(sourcePending, { force: true });
  process.stdout.write(`[research-cache] recorded ${Object.keys(snapshot.articles).length} complete article bundles\n`);
}

export function finalizeResearchBuild() {
  finalizeResearchSnapshot(readJson(pendingPath));
}
