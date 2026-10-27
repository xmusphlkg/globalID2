#!/usr/bin/env node
// Generate src/lib/data/world-feature-iso.json.
//
// The bundled world map only exposes a display name per polygon, while every
// surveillance record is keyed by ISO 3166-1 alpha-2 code. Resolving that link
// once at authoring time keeps the 52 kB "flag-icons" catalogue out of the
// client bundle and keeps the lookup deterministic across locales and builds.
//
// Usage: node scripts/build-world-feature-iso.mjs [--check]
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { gzipSync } from 'node:zlib';
import { buildFeatureNameToIso } from '../src/lib/worldMapNames.ts';

const siteRoot = resolve(import.meta.dirname, '..');
const sourcePath = resolve(siteRoot, 'public', 'data', 'world.json');
const outputPath = resolve(siteRoot, 'src', 'lib', 'data', 'world-feature-iso.json');
const checkOnly = process.argv.includes('--check');

const features = JSON.parse(readFileSync(sourcePath, 'utf8')).features ?? [];
const catalogue = createRequire(import.meta.url)('flag-icons/country.json');
const { byFeatureName, unresolvedFeatureNames } = buildFeatureNameToIso(features, catalogue);

const codes = Object.values(byFeatureName);
const duplicates = codes.filter((code, index) => codes.indexOf(code) !== index);
if (duplicates.length) {
  throw new Error(`Duplicate country codes resolved on the world map: ${[...new Set(duplicates)].join(', ')}`);
}

// The surveillance corpus only publishes these territories. Anything else that
// stays unresolved is a genuine naming gap rather than a coverage regression.
const IGNORED_UNRESOLVED = new Set(['Antarctica', 'Fr. S. Antarctic Lands', 'N. Cyprus', 'Somaliland', 'Kosovo', 'Siachen Glacier']);
const unexpected = unresolvedFeatureNames.filter((name) => !IGNORED_UNRESOLVED.has(name));
if (unexpected.length) {
  throw new Error(`Unresolved world map features: ${unexpected.join(', ')}`);
}

const payload = { generatedFrom: 'public/data/world.json', featureNameToIso: byFeatureName };
const serialised = `${JSON.stringify(payload, null, 2)}\n`;

if (checkOnly) {
  if (readFileSync(outputPath, 'utf8') !== serialised) {
    throw new Error('world-feature-iso.json is stale; run npm run map:iso');
  }
  console.log(`[world-feature-iso] up to date (${Object.keys(byFeatureName).length} features)`);
} else {
  writeFileSync(outputPath, serialised);
  console.log(
    `[world-feature-iso] ${Object.keys(byFeatureName).length} features -> ${serialised.length} bytes (${gzipSync(Buffer.from(serialised)).length} gzipped)`,
  );
}
