import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileHash } from './file-fingerprints.mjs';
import { factDigest, inputSignature, projectCountry, projectDisease } from '../src/lib/directory-facts.ts';

const site = resolve(import.meta.dirname, '..');
export const directoryFactsPath = (dataRoot = resolve(site, 'src/data')) =>
  resolve(site, '../data/cache', `site-directory-${factDigest(resolve(dataRoot)).slice(0, 16)}.json`);
export function directoryFactsVersion() {
  return factDigest(['scripts/directory-build-cache.mjs', 'src/lib/directory-facts.ts',
    'src/lib/country-disease-candidates.ts', 'src/lib/country-coverage.ts', 'src/lib/seo.ts', 'src/lib/papers.ts']
    .map(path => fileHash(resolve(site, path))));
}
export function openDirectoryBuildCache(dataRoot, { path = directoryFactsPath(dataRoot), version = directoryFactsVersion(), hash = fileHash,
  read = path => JSON.parse(readFileSync(path, 'utf8')) } = {}) {
  dataRoot = resolve(dataRoot);
  let previous = {};
  try {
    const saved = JSON.parse(readFileSync(path, 'utf8'));
    if (saved.schema === 1 && saved.version === version && saved.dataRoot === dataRoot) previous = saved.entries;
  } catch { /* Missing/corrupt cache is recomputed. */ }
  const entries = {};
  const stats = { hits: 0, misses: 0, reads: 0 };
  const project = (key, paths, derive) => {
    const inputs = paths.map(path => ({ path, stat: inputSignature(path), sha256: existsSync(path) ? hash(path) : 'missing' }));
    const old = entries[key] ?? previous?.[key];
    let value;
    let reusable = false;
    try {
      reusable = Boolean(old && Array.isArray(old.inputs) && old.inputs.length === inputs.length
        && old.inputs.every((input, index) => input.path === inputs[index].path && input.sha256 === inputs[index].sha256)
        && old.digest === factDigest(old.value));
    } catch { /* Malformed entries must not fail an otherwise valid build. */ }
    if (reusable) {
      stats.hits++; value = old.value;
    } else {
      stats.misses++;
      value = derive(...inputs.map(input => {
        if (input.stat === 'missing') return null;
        stats.reads++; return read(input.path);
      }));
    }
    for (const input of inputs) if (inputSignature(input.path) !== input.stat) throw new Error(`Directory input changed while reading: ${input.path}`);
    entries[key] = { inputs, value, digest: factDigest(value) };
    return value;
  };
  return {
    stats, path, version,
    country: code => project(`country:${code.toLowerCase()}`, [resolve(dataRoot, 'countries', `${code.toLowerCase()}.json`)], record => projectCountry(code.toLowerCase(), record)),
    disease: id => project(`disease:${id.toLowerCase()}`, [resolve(dataRoot, 'diseases', `${id.toLowerCase()}.json`), resolve(dataRoot, 'disease-knowledge', `${id.toLowerCase()}.json`)], projectDisease),
    save() {
      mkdirSync(dirname(path), { recursive: true });
      const temporary = `${path}.${process.pid}.tmp`;
      try {
        writeFileSync(temporary, JSON.stringify({ schema: 1, version, dataRoot, entries }));
        renameSync(temporary, path);
      } finally { rmSync(temporary, { force: true }); }
    },
  };
}
