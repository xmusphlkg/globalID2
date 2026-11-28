// Cache extracted page facts, not validation decisions. Global budgets and
// dependency graphs are evaluated against every current output catalogue.
import { createHash } from 'node:crypto';
import { readFileSync, mkdirSync, renameSync, statSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileHash } from './file-fingerprints.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
const signature = file => {
  const stat = statSync(file, { bigint: true });
  return `${stat.dev}:${stat.ino}:${stat.size}:${stat.mtimeNs}:${stat.ctimeNs}`;
};
export function openPageAuditCache(path, version) {
  let saved = {};
  try { const value = JSON.parse(readFileSync(path, 'utf8')); if (value.version === version) saved = value.files ?? {}; } catch {}
  const next = {};
  const stats = { hits: 0, misses: 0 };
  const store = (file, digest, value) => {
    next[file] = { digest, value, checksum: hash(JSON.stringify(value)) };
    stats.misses++;
    return value;
  };
  const tryHit = file => {
    const entry = saved[file];
    if (!entry) return undefined;
    const digest = fileHash(file);
    if (entry.digest === digest && entry.checksum === hash(JSON.stringify(entry.value))) {
      next[file] = entry;
      stats.hits++;
      return entry.value;
    }
    return undefined;
  };
  return {
    tryHit,
    store,
    read(file, extract) {
      const entry = saved[file];
      if (entry) {
        const digest = fileHash(file);
        if (entry.digest === digest && entry.checksum === hash(JSON.stringify(entry.value))) {
          next[file] = entry; stats.hits++; return entry.value;
        }
        const body = readFileSync(file);
        if (hash(body) !== digest) throw new Error(`Audit input changed while reading: ${file}`);
        return store(file, digest, extract(body, digest));
      }
      const before = signature(file);
      const body = readFileSync(file);
      if (signature(file) !== before) throw new Error(`Audit input changed while reading: ${file}`);
      const digest = hash(body);
      return store(file, digest, extract(body, digest));
    },
    save() {
      mkdirSync(dirname(path), { recursive: true });
      const temporary = `${path}.${process.pid}.tmp`;
      writeFileSync(temporary, JSON.stringify({ version, files: next }));
      renameSync(temporary, path);
    },
    stats,
  };
}
