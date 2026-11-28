import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { gzipSync } from 'node:zlib';

// Cache computed sizes, never pass/fail decisions: every run still evaluates
// its current budgets and asset graph. Content changes always get a new key.
export function openGzipSizeCache(path, { compress = bytes => gzipSync(bytes, { level: 9 }).length } = {}) {
  const algorithm = `gzip:9:zlib:${process.versions.zlib}`;
  let previous = {};
  try {
    const saved = JSON.parse(readFileSync(path, 'utf8'));
    if (saved.schema === 1 && saved.algorithm === algorithm && saved.entries && !Array.isArray(saved.entries)) previous = saved.entries;
  } catch { /* Missing or damaged caches are recomputed. */ }
  const entries = {};
  const stats = { hits: 0, misses: 0 };
  const lookup = (key, rawBytes) => {
    const saved = entries[key] ?? previous[key];
    if (Array.isArray(saved) && saved[0] === rawBytes && Number.isSafeInteger(saved[1]) && saved[1] > 0 && saved[1] <= rawBytes + 1024) {
      stats.hits += 1;
      entries[key] = saved;
      return saved[1];
    }
    return undefined;
  };
  const record = (key, rawBytes, size) => {
    stats.misses += 1;
    entries[key] = [rawBytes, size];
    return size;
  };
  const snapshot = () => ({ ...previous, ...entries });
  return {
    stats,
    lookup,
    record,
    snapshot,
    measure(bytes, knownKey) {
      const key = knownKey ?? createHash('sha256').update(bytes).digest('hex');
      const hit = lookup(key, bytes.length);
      if (hit !== undefined) return hit;
      return record(key, bytes.length, compress(bytes));
    },
    save() {
      mkdirSync(dirname(path), { recursive: true });
      const temporary = `${path}.${process.pid}.tmp`;
      const body = JSON.stringify({ schema: 1, algorithm, entries });
      if (existsSync(path) && readFileSync(path, 'utf8') === body) return;
      try {
        writeFileSync(temporary, body);
        renameSync(temporary, path);
      } finally {
        rmSync(temporary, { force: true });
      }
    },
  };
}
