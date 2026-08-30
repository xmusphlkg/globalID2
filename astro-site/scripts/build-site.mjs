import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { writeBuildRedirects } from './build-redirects.mjs';

const projectRoot = resolve(import.meta.dirname, '..', '..');
const settingsPath = resolve(projectRoot, 'data', 'system-settings.json');
const researchIndexPath = resolve(import.meta.dirname, '..', 'src', 'data', 'research', 'index.json');
const defaultMeasurementId = 'G-8P39XV52NC';
const measurementPattern = /^G-[A-Z0-9]{6,20}$/i;

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
const result = spawnSync(astroBinary, ['build'], {
  cwd: resolve(import.meta.dirname, '..'),
  env: {
    ...process.env,
    PUBLIC_GA4_MEASUREMENT_ID: measurementId,
  },
  stdio: 'inherit',
});

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
process.exit(result.status ?? 1);
