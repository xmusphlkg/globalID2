/** Public paper template primitives. No route is activated by this module. */
export type PaperTemplateSlot = { field: 'slug' | 'main' } | { field: 'head'; index: number };
export type PaperTemplate = { schema: 1; chunks: Array<string | PaperTemplateSlot> };
export type PaperRecord = { schema: 1; slug: string; head: string[]; main: string };

/** Assemble only pre-rendered public fragments. Validate every field and slot;
 * callers must obtain both objects from trusted, bounded R2 publications. */
export function assemblePaper(template: PaperTemplate, record: PaperRecord): string {
  if (template?.schema !== 1 || record?.schema !== 1
      || !Array.isArray(template.chunks) || !Array.isArray(record.head)
      || typeof record.main !== 'string' || typeof record.slug !== 'string'
      || !/^[a-z0-9][a-z0-9-]*$/.test(record.slug)) throw new Error('Invalid paper template or record');
  return template.chunks.map(chunk => {
    if (typeof chunk === 'string') return chunk;
    if (!chunk || typeof chunk !== 'object') throw new Error('Invalid paper template slot');
    const keys = Object.keys(chunk);
    if (chunk.field === 'slug' && keys.length === 1) return record.slug;
    if (chunk.field === 'main' && keys.length === 1) return record.main;
    if (chunk.field === 'head' && keys.length === 2 && Number.isSafeInteger(chunk.index)
        && chunk.index >= 0 && typeof record.head[chunk.index] === 'string') return record.head[chunk.index];
    throw new Error('Invalid paper template slot');
  }).join('');
}
