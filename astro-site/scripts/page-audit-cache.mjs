// Cache extracted page facts, not validation decisions. Global budgets and
// dependency graphs are evaluated against every current output catalogue.
import { createHash } from 'node:crypto';
import { readFileSync, mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileHash } from './file-fingerprints.mjs';
const hash = value => createHash('sha256').update(value).digest('hex');
export function openPageAuditCache(path, version) {
  let saved = {};
  try { const value = JSON.parse(readFileSync(path, 'utf8')); if (value.version === version) saved = value.files ?? {}; } catch {}
  const next = {};
  const stats = { hits: 0, misses: 0 };
  return {
    read(file, extract) {
      const digest = fileHash(file);
      const entry = saved[file];
      if (entry?.digest === digest && entry.checksum === hash(JSON.stringify(entry.value))) {
        next[file] = entry; stats.hits++; return entry.value;
      }
      const body = readFileSync(file);
      if (hash(body) !== digest) throw new Error(`Audit input changed while reading: ${file}`);
      const value = extract(body);
      next[file] = { digest, value, checksum: hash(JSON.stringify(value)) };
      stats.misses++;
      return value;
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
