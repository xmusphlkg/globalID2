import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const WORLD_MAP_COORDINATE_PRECISION = 4;

function roundCoordinates(value, precision) {
  if (typeof value === 'number') return Number(value.toFixed(precision));
  if (Array.isArray(value)) return value.map((item) => roundCoordinates(item, precision));
  return value;
}

export function compactWorldMap(payload, precision = WORLD_MAP_COORDINATE_PRECISION) {
  if (payload?.type !== 'FeatureCollection' || !Array.isArray(payload.features)) {
    throw new TypeError('World map must be a GeoJSON FeatureCollection.');
  }
  return {
    ...payload,
    features: payload.features.map((feature) => ({
      ...feature,
      geometry: feature?.geometry
        ? {
            ...feature.geometry,
            coordinates: roundCoordinates(feature.geometry.coordinates, precision),
          }
        : feature?.geometry,
    })),
  };
}

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? resolve(process.argv[index + 1]) : fallback;
}

function runCli() {
  const source = argument('--source', resolve(import.meta.dirname, '..', 'public', 'data', 'world.json'));
  const output = argument('--output', resolve(import.meta.dirname, '..', 'dist', 'data', 'world.json'));
  if (!existsSync(source)) throw new Error(`World map source not found: ${source}`);
  if (!existsSync(dirname(output))) throw new Error(`World map output directory not found: ${dirname(output)}`);

  const sourceBytes = readFileSync(source);
  const sourcePayload = JSON.parse(sourceBytes.toString('utf8'));
  const compactPayload = compactWorldMap(sourcePayload);
  const outputBytes = Buffer.from(JSON.stringify(compactPayload));
  if (compactPayload.features.length !== sourcePayload.features.length) {
    throw new Error('World map feature count changed during compaction.');
  }
  if (outputBytes.length >= sourceBytes.length) {
    throw new Error('World map compaction did not reduce the source artifact.');
  }

  const temporary = `${output}.tmp`;
  writeFileSync(temporary, outputBytes);
  renameSync(temporary, output);
  process.stdout.write(
    `[world-map] ${compactPayload.features.length} features, ${sourceBytes.length} -> ${outputBytes.length} bytes\n`,
  );
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) runCli();
