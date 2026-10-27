import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeBuildRedirects } from './build-redirects.mjs';
import { planPath, planResearchBuild, restoreReusablePages, reusedAssetReferencesAvailable, sourceSnapshot, stageReusablePages, writePending } from './research-build-cache.mjs';

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
const snapshot = sourceSnapshot();
let plan = planResearchBuild({
  incremental: Boolean(process.env.PUBLIC_GIDS_RELEASE_ID || process.env.GIDS_RESEARCH_INCREMENTAL === '1'),
  snapshot,
});
const elapsedSeconds = started => ((performance.now() - started) / 1000).toFixed(1);
const stagingStarted = performance.now();
const stage = plan.reused.length ? stageReusablePages(plan) : null;
process.stdout.write(`[build-timing] stage reusable research pages ${elapsedSeconds(stagingStarted)}s\n`);
mkdirSync(resolve(projectRoot, 'data', 'cache'), { recursive: true });
writeFileSync(planPath, JSON.stringify({ selected: plan.selected }));
process.stdout.write(`[research-cache] ${plan.mode}: render ${plan.selected.length}, reuse ${plan.reused.length} article bundles\n`);

const runAstro = selectedPath => {
  const started = performance.now();
  const result = spawnSync(astroBinary, ['build'], {
    cwd: resolve(import.meta.dirname, '..'),
    env: {
      ...process.env,
      PUBLIC_GA4_MEASUREMENT_ID: measurementId,
      ...(selectedPath ? { GIDS_RESEARCH_BUILD_PLAN: selectedPath } : { GIDS_RESEARCH_BUILD_PLAN: '' }),
    },
    stdio: 'inherit',
  });
  process.stdout.write(`[build-timing] Astro render ${elapsedSeconds(started)}s status=${result.status ?? 'error'}\n`);
  return result;
};
let result = runAstro(plan.mode === 'incremental' ? planPath : null);

if ((result.status ?? 1) === 0 && stage) {
  const restoreStarted = performance.now();
  restoreReusablePages(plan, stage);
  process.stdout.write(`[build-timing] restore reusable research pages ${elapsedSeconds(restoreStarted)}s\n`);
  if (!reusedAssetReferencesAvailable(plan)) {
    process.stdout.write('[research-cache] cached page asset missing; rebuilding all article bundles\n');
    result = runAstro(null);
    plan = { ...plan, mode: 'full', selected: Object.keys(snapshot.articles), reused: [] };
  }
}
if (stage) rmSync(stage, { recursive: true, force: true });
rmSync(planPath, { force: true });

if (result.error) {
  process.stderr.write(`${result.error.message}\n`);
  process.exit(1);
}
if ((result.status ?? 1) === 0) {
  const redirects = writeBuildRedirects();
  process.stdout.write(`[redirects] generated ${redirects.generatedRules} legacy situation rules\n`);
}
const researchFingerprintAfter = contentFingerprint(researchIndexPath);
if (researchFingerprintAfter !== researchFingerprintBefore) {
  process.stderr.write(
    'Research Radar source data changed during the Astro build; retry after the publication task finishes.\n',
  );
  process.exit(3);
}
if ((result.status ?? 1) === 0 && JSON.stringify(sourceSnapshot()) !== JSON.stringify(snapshot)) {
  process.stderr.write('Research detail data or shared templates changed during the Astro build.\n');
  process.exit(3);
}
if ((result.status ?? 1) === 0) writePending(plan);
process.exit(result.status ?? 1);
