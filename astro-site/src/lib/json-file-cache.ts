import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

/** Bounded parsed-file cache. Readers must treat returned data as read-only. */
export function createJsonFileCache({ maxBytes = 64 * 1024 * 1024, maxFileBytes = 16 * 1024 * 1024,
  read = (path: string) => readFileSync(path, 'utf8'), now = () => Date.now() } = {}) {
  const entries = new Map<string, { signature: string; bytes: number; value: unknown; stable: boolean }>();
  let bytes = 0;
  const signature = (path: string) => {
    const stat = statSync(path, { bigint: true });
    const cutoff = BigInt(now() - 1000) * 1000000n;
    return { key: [stat.dev, stat.ino, stat.size, stat.mtimeNs, stat.ctimeNs].join(':'), size: Number(stat.size),
      stable: stat.mtimeNs < cutoff && stat.ctimeNs < cutoff };
  };
  return {
    read<T = unknown>(path: string): T {
      const key = resolve(path);
      const before = signature(key);
      const cached = entries.get(key);
      if (cached?.stable && cached.signature === before.key) {
        entries.delete(key); entries.set(key, cached);
        return cached.value as T;
      }
      if (cached) { entries.delete(key); bytes -= cached.bytes; }
      const value = JSON.parse(read(key));
      if (signature(key).key !== before.key) throw new Error(`JSON input changed while reading: ${key}`);
      if (before.size <= Math.min(maxBytes, maxFileBytes)) {
        while (bytes + before.size > maxBytes && entries.size) {
          const oldest = entries.keys().next().value!;
          bytes -= entries.get(oldest)!.bytes; entries.delete(oldest);
        }
        entries.set(key, { signature: before.key, bytes: before.size, value, stable: before.stable });
        bytes += before.size;
      }
      return value as T;
    },
    get bytes() { return bytes; },
    get size() { return entries.size; },
  };
}

export const jsonFileCache = createJsonFileCache();
