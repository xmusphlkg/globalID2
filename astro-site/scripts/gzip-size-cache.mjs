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
  return {
    stats,
    measure(bytes) {
      const key = createHash('sha256').update(bytes).digest('hex');
      const saved = entries[key] ?? previous[key];
      if (Array.isArray(saved) && saved[0] === bytes.length && Number.isSafeInteger(saved[1]) && saved[1] > 0 && saved[1] <= bytes.length + 1024) {
        stats.hits += 1;
        entries[key] = saved;
        return saved[1];
      }
      stats.misses += 1;
      const size = compress(bytes);
      entries[key] = [bytes.length, size];
      return size;
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
