import { resolve, sep } from 'node:path';
import { createJsonFileCache } from './json-file-cache.ts';
import type { ResearchFeedData } from './research-feed.ts';

// Generated payloads are runtime inputs, never TypeScript/Vite source modules.
// Static builds consume one frozen snapshot; development rechecks file identity.
export function createGeneratedDataReader(root: string, isBuild = () => process.env.GIDS_STATIC_RENDER === '1') {
  root = resolve(root);
  const cache = createJsonFileCache({ maxBytes: 384 * 1024 * 1024, maxFileBytes: 320 * 1024 * 1024 });
  const snapshot = new Map<string, Record<string, any>>();
  return <T extends object = Record<string, any>>(name: string): T => {
    const path = resolve(root, name);
    if (!path.startsWith(root + sep) || !path.endsWith('.json')) throw new Error(`Invalid generated data path: ${name}`);
    if (isBuild() && snapshot.has(path)) return snapshot.get(path) as T;
    const value = cache.read<Record<string, any>>(path);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Expected generated object: ${name}`);
    if (isBuild()) snapshot.set(path, value);
    return value as T;
  };
}

export const readGeneratedObject = createGeneratedDataReader(resolve('./src/data'));
export const readResearchIndex = () => readGeneratedObject<ResearchFeedData & Record<string, any>>('research/index.json');
