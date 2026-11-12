import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/** Read generated research fields from bounded files instead of one huge JSON string. */
export function readResearchPublicFields(root: string, fields?: string[]): Record<string, any> {
  const directory = resolve(root);
  const index = JSON.parse(readFileSync(resolve(directory, 'index.json'), 'utf8'));
  const load = (name: string) => {
    if (!/^shards\/[A-Za-z0-9_-]+\.json$/.test(name)) throw new Error('Invalid research shard path');
    return JSON.parse(readFileSync(resolve(directory, name), 'utf8'));
  };
  const expand = (value: any): any => {
    if (!value || typeof value !== 'object' || value.sharded !== true) return value;
    if (value.kind === 'value' && typeof value.file === 'string') return load(value.file).value;
    if (value.kind === 'list' && Array.isArray(value.chunks)) {
      const result: any[] = [];
      for (const chunk of value.chunks) {
        const body = load(chunk.file);
        if (!Array.isArray(body.records)) throw new Error('Invalid research shard');
        for (const record of body.records) result.push(record);
      }
      return result;
    }
    if (value.kind === 'object' && value.parts && typeof value.parts === 'object')
      return Object.fromEntries(Object.entries(value.parts).map(([key, child]) => [key, expand(child)]));
    throw new Error('Invalid research shard descriptor');
  };
  const keys = fields ?? Object.keys(index);
  return Object.fromEntries(keys.filter(key => Object.hasOwn(index, key)).map(key => [key, expand(index[key])]));
}
