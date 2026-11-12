export interface ResearchQueryEntity { scope: 'disease' | 'country' | 'topic'; value: string; aliases: string[]; }
const normalize = (value: string) => value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
const matches = (query: string, alias: string) => /[\u3400-\u9fff]/.test(alias) ? query.includes(alias) : (` ${query} `).includes(` ${alias} `);

/** Resolve a complete published collection before loading its article payload. */
export function researchQueryScopes(query: string, entities: ResearchQueryEntity[]): ResearchQueryEntity[] {
  const text = normalize(query);
  for (const scope of ['disease', 'country', 'topic'] as const) {
    const found = entities.filter(entity => entity.scope === scope).flatMap(entity => {
      const aliases = entity.aliases.map(normalize).filter(alias => alias.length >= 2 && matches(text, alias));
      return aliases.length ? [{ entity, alias: aliases.sort((a, b) => b.length - a.length)[0] }] : [];
    });
    const specific = found.filter(item => !found.some(other => other !== item && other.alias.length > item.alias.length && other.alias.includes(item.alias)));
    if (specific.length) return specific.map(item => item.entity);
  }
  return [];
}
