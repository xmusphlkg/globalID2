import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const projectRoot = resolve(import.meta.dirname, '..', '..');
const settingsPath = resolve(projectRoot, 'data', 'system-settings.json');
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
process.exit(result.status ?? 1);
