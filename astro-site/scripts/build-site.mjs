import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { directoryFactsPath, directoryFactsVersion } from './directory-build-cache.mjs';
import { writeBuildRedirects } from './build-redirects.mjs';
import { astroAssetCacheRoot, incrementalBuildEnabled, mergeAstroAssetTree, planPath, planResearchBuild, replaceAstroAssetCache, restoreReusablePages, reusedAssetReferencesAvailable, sourceSnapshot, sharedFingerprint, summarizeBuildPlan, writePending } from './research-build-cache.mjs';
import { pageSourceSnapshot, planSitePages, pagePlanPath, sitePageCacheRoot, writePagePending } from './page-build-cache.mjs';
import { preparePublicBuildInputs } from './public-build-inputs.mjs';

const projectRoot = resolve(import.meta.dirname, '..', '..');
const settingsPath = resolve(projectRoot, 'data', 'system-settings.json');
const researchIndexPath = resolve(import.meta.dirname, '..', 'src', 'data', 'research', 'index.json');
const researchPublicArtifactLockPath = resolve(projectRoot, 'data', 'cache', 'research-public-artifacts.lock');
const defaultMeasurementId = 'G-8P39XV52NC';
const measurementPattern = /^G-[A-Z0-9]{6,20}$/i;

if (process.env.GIDS_RESEARCH_BUILD_LOCKED !== '1') {
  mkdirSync(resolve(projectRoot, 'data', 'cache'), { recursive: true });
  const locked = spawnSync('flock', [
    '-x',
    researchPublicArtifactLockPath,
    process.execPath,
    fileURLToPath(import.meta.url),
  ], {
    cwd: resolve(import.meta.dirname, '..'),
    env: { ...process.env, GIDS_RESEARCH_BUILD_LOCKED: '1' },
    stdio: 'inherit',
  });
  if (locked.error) {
    process.stderr.write(`${locked.error.message}\n`);
    process.exit(1);
  }
  process.exit(locked.status ?? 1);
}

function configuredMeasurementId() {
  if (process.env.PUBLIC_GA4_MEASUREMENT_ID !== undefined) {
    return process.env.PUBLIC_GA4_MEASUREMENT_ID.trim().toUpperCase();
  }
  if (!existsSync(settingsPath)) return defaultMeasurementId;
  try {
    const settings = JSON.parse(readFileSync(settingsPath, 'utf8'));
    if (Object.prototype.hasOwnProperty.call(settings?.site ?? {}, 'public_ga4_measurement_id')) {
      return String(settings.site.public_ga4_measurement_id ?? '').trim().toUpperCase();
    }
  } catch (error) {
    process.stderr.write(`Warning: unable to read ${settingsPath}: ${error.message}\n`);
  }
  return defaultMeasurementId;
}

const measurementId = configuredMeasurementId();
if (measurementId && !measurementPattern.test(measurementId)) {
  process.stderr.write('Invalid PUBLIC_GA4_MEASUREMENT_ID. Expected G-XXXXXXXXXX.\n');
  process.exit(2);
}

function contentFingerprint(path) {
  if (!existsSync(path)) return 'missing';
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

// A Research Radar automation task can publish a new source snapshot while an
// Astro build is reading static inputs.  Failing the build is safer than
// shipping pages assembled from two different snapshots; the release can be
// retried immediately after the publisher finishes.
const researchFingerprintBefore = contentFingerprint(researchIndexPath);

const astroBinary = resolve(
  import.meta.dirname,
  '..',
  'node_modules',
  '.bin',
  process.platform === 'win32' ? 'astro.cmd' : 'astro',
);
const planningStarted = performance.now();
const independentResearch = process.env.GIDS_RESEARCH_PUBLICATION_ENABLED === '1';
const snapshot = independentResearch ? { schema: 1, shared: 'independent', articles: {} } : sourceSnapshot();
const directoryStats = {};
const pageSnapshot = pageSourceSnapshot({ directoryStats });
process.stdout.write(`[directory-cache] ${JSON.stringify(directoryStats)}\n`);
const incremental = incrementalBuildEnabled();
let pagePlan = planSitePages(pageSnapshot, incremental);
let plan = independentResearch ? { mode: 'incremental', selected: [], reused: [], removed: [], reasons: {}, snapshot } : planResearchBuild({
  incremental,
  snapshot,
});
const assetCachePath = resolve(astroAssetCacheRoot, sharedFingerprint());
const elapsedSeconds = started => ((performance.now() - started) / 1000).toFixed(1);
mkdirSync(resolve(projectRoot, 'data', 'cache'), { recursive: true });
writeFileSync(planPath, JSON.stringify({ selected: plan.selected }));
writeFileSync(pagePlanPath, JSON.stringify({ selected: pagePlan.selected, known: Object.keys(pageSnapshot.articles) }));
process.stdout.write(`[research-cache] ${plan.mode}: render ${plan.selected.length}, reuse ${plan.reused.length} article bundles\n`);
process.stdout.write(`[page-cache] ${pagePlan.mode}: render ${pagePlan.selected.length}, reuse ${pagePlan.reused.length} collection/country/disease bundles\n`);
process.stdout.write(`[build-timing] plan inputs and verify cache ${elapsedSeconds(planningStarted)}s\n`);
for (const [label, value] of [['research', plan], ['pages', pagePlan]]) {
  process.stdout.write(`[build-plan] ${label} ${JSON.stringify(summarizeBuildPlan(value))}\n`);
}
writeFileSync(resolve(projectRoot, 'data/cache/site-build-plan-report.json'), JSON.stringify({
  research: { ...summarizeBuildPlan(plan), reasons: plan.reasons, removed: plan.removed },
  pages: { ...summarizeBuildPlan(pagePlan), reasons: pagePlan.reasons, removed: pagePlan.removed },
}));

const runAstro = (selectedPath, selectedPagePath) => {
  const started = performance.now();
  const publicInputs = preparePublicBuildInputs({
    publicRoot: resolve(import.meta.dirname, '..', 'public'),
    stageRoot: resolve(projectRoot, 'data/cache/public-build-inputs', String(process.pid)),
    external: process.env.GIDS_EXTERNAL_SURVEILLANCE_DATA === '1',
  });
  let result;
  try { result = spawnSync(astroBinary, ['build'], {
    cwd: resolve(import.meta.dirname, '..'),
    env: {
      ...process.env,
      GIDS_STATIC_RENDER: '1',
      GIDS_SITE_DIRECTORY_FACTS: directoryFactsPath(),
      GIDS_SITE_DIRECTORY_VERSION: directoryFactsVersion(),
      PUBLIC_GA4_MEASUREMENT_ID: measurementId,
      ...(publicInputs.staged ? { GIDS_BUILD_PUBLIC_DIR: publicInputs.publicDir } : {}),
      ...(selectedPath ? { GIDS_RESEARCH_BUILD_PLAN: selectedPath } : { GIDS_RESEARCH_BUILD_PLAN: '' }),
      GIDS_PAGE_BUILD_PLAN: selectedPagePath ?? '',
    },
    stdio: 'inherit',
  }); } finally { publicInputs.cleanup(); }
  process.stdout.write(`[build-timing] Astro render ${elapsedSeconds(started)}s status=${result.status ?? 'error'}\n`);
  return result;
};
let result = runAstro(plan.mode === 'incremental' ? planPath : null, pagePlan.mode === 'incremental' ? pagePlanPath : null);

if ((result.status ?? 1) === 0 && (plan.reused.length || pagePlan.reused.length)) {
  const restoreStarted = performance.now();
  restoreReusablePages(plan);
  restoreReusablePages(pagePlan, sitePageCacheRoot);
  process.stdout.write(`[build-timing] restore reusable pages ${elapsedSeconds(restoreStarted)}s\n`);
  const assetsStarted = performance.now();
  const restoredAssets = mergeAstroAssetTree(assetCachePath, resolve(import.meta.dirname, '..', 'dist', '_astro'));
  process.stdout.write(`[asset-cache] restored ${restoredAssets} shared assets\n`);
  const available = reusedAssetReferencesAvailable(plan) && reusedAssetReferencesAvailable(pagePlan);
  process.stdout.write(`[build-timing] restore and validate shared assets ${elapsedSeconds(assetsStarted)}s\n`);
  if (!available) {
    process.stdout.write('[page-cache] cached page asset missing; rebuilding all page bundles\n');
    result = runAstro(null, null);
    plan = { ...plan, mode: 'full', selected: Object.keys(snapshot.articles), reused: [] };
    pagePlan = { ...pagePlan, mode: 'full', selected: Object.keys(pageSnapshot.articles), reused: [] };
  }
}
rmSync(planPath, { force: true });
rmSync(pagePlanPath, { force: true });

if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}
if ((result.status ?? 1) === 0) {
  const redirects = writeBuildRedirects();
  process.stdout.write(`[redirects] generated ${redirects.generatedRules} legacy situation rules\n`);
}
const researchFingerprintAfter = contentFingerprint(researchIndexPath);
const recheckStarted = performance.now();
if (researchFingerprintAfter !== researchFingerprintBefore) {
  process.stderr.write(
    'Research Radar source data changed during the Astro build; retry after the publication task finishes.\n',
  );
  process.exit(3);
}
if ((result.status ?? 1) === 0 && ((!independentResearch && JSON.stringify(sourceSnapshot()) !== JSON.stringify(snapshot))
  || JSON.stringify(pageSourceSnapshot()) !== JSON.stringify(pageSnapshot))) {
  process.stderr.write('Research detail data or shared templates changed during the Astro build.\n');
  process.exit(3);
}
if ((result.status ?? 1) === 0) {
  process.stdout.write(`[build-timing] recheck source consistency ${elapsedSeconds(recheckStarted)}s\n`);
  const cacheStarted = performance.now();
  const cachedAssets = replaceAstroAssetCache(
    resolve(import.meta.dirname, '..', 'dist', '_astro'),
    assetCachePath,
  );
  process.stdout.write(`[asset-cache] saved ${cachedAssets} shared assets\n`);
  if (!independentResearch) writePending(plan);
  writePagePending(pagePlan);
  process.stdout.write(`[build-timing] save shared assets and pending plans ${elapsedSeconds(cacheStarted)}s\n`);
}
process.exit(result.status ?? 1);
