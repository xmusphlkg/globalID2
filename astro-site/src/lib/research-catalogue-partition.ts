/** Each record appears once; Ask can skip bibliography-only chunks. */
export function partitionResearchCatalogue<T extends { study_type?: string; detail_available?: boolean }>(articles: T[], size = 100): T[][] {
  if (!Number.isInteger(size) || size < 1) throw new Error('Invalid research chunk size');
  const groups = new Map<string, T[]>();
  for (const article of articles) {
    const key = `${article.detail_available === false ? 'bibliographic' : 'reviewed'}:${String(article.study_type ?? '').trim().toLowerCase()}`;
    const group = groups.get(key) ?? [];
    group.push(article);
    groups.set(key, group);
  }
  return [...groups.keys()].sort().flatMap(key => {
    const group = groups.get(key)!;
    return Array.from({ length: Math.ceil(group.length / size) }, (_, i) => group.slice(i * size, (i + 1) * size));
  });
}
