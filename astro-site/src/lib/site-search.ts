import { normalizeSearchText } from './disease-search.ts';

export type SearchLocale = 'en' | 'zh' | 'fr';
export type SiteSearchEntry = {
  id: string;
  kind: string;
  title: { en?: string; zh?: string; fr?: string };
  summary?: { en?: string; zh?: string; fr?: string };
  href: { en: string; zh?: string; fr?: string };
  aliases?: string[];
};

export function searchTitle(entry: SiteSearchEntry, locale: SearchLocale): string {
  return entry.title[locale] || entry.title.en || '';
}
export function searchSummary(entry: SiteSearchEntry, locale: SearchLocale): string {
  return entry.summary?.[locale] || entry.summary?.en || '';
}
export function rankSiteSearch(entry: SiteSearchEntry, queryValue: string, locale: SearchLocale): number {
  const query = normalizeSearchText(queryValue);
  if (!query) return 0;
  const title = normalizeSearchText(searchTitle(entry, locale));
  const aliases = (entry.aliases ?? []).map(normalizeSearchText);
  if (title === query) return 120;
  if (aliases.includes(query)) return 110;
  if (title.startsWith(query)) return 95;
  if (aliases.some(alias => alias.startsWith(query))) return 80;
  if (title.includes(query)) return 70;
  if (aliases.some(alias => alias.includes(query))) return 45;
  if (normalizeSearchText(searchSummary(entry, locale)).includes(query)) return 20;
  return 0;
}

function containedAlias(query: string, entry: SiteSearchEntry): string | undefined {
  return [...Object.values(entry.title), ...(entry.aliases ?? [])].filter((alias): alias is string => Boolean(alias))
    .map(normalizeSearchText).filter(alias => alias.length >= 2)
    .sort((a, b) => b.length - a.length).find(alias => {
      if (!query.includes(alias)) return false;
      // Keep short Latin codes out of unrelated words (e.g. US in virus).
      if (/^[a-z0-9 -]+$/.test(alias)) return new RegExp(`(^|[^a-z0-9])${alias.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}($|[^a-z0-9])`).test(query);
      return true;
    });
}

/** Offer the query-aware research/monitoring tool for combined entity queries. */
export function compoundSearchEntry(entries: SiteSearchEntry[], queryValue: string): SiteSearchEntry | undefined {
  const query = normalizeSearchText(queryValue);
  if (query.length < 3) return undefined;
  const country = entries.filter(entry => entry.kind === 'country').map(entry => ({ entry, alias: containedAlias(query, entry) }))
    .filter(item => item.alias).sort((a, b) => b.alias!.length - a.alias!.length)[0];
  if (!country) return undefined;
  const remaining = query.replace(country.alias!, ' ').trim();
  const disease = entries.filter(entry => entry.kind === 'disease').map(entry => ({ entry, alias: containedAlias(remaining, entry) }))
    .filter(item => item.alias).sort((a, b) => b.alias!.length - a.alias!.length)[0];
  if (!disease) return undefined;
  const path = `/research/ask/?q=${encodeURIComponent(queryValue.trim())}`;
  return {
    id: `focused:${country.entry.id}:${disease.entry.id}`,
    kind: 'page',
    title: {
      en: `${searchTitle(disease.entry, 'en')} in ${searchTitle(country.entry, 'en')}`,
      zh: `${searchTitle(country.entry, 'zh')} · ${searchTitle(disease.entry, 'zh')}`,
      fr: `${searchTitle(disease.entry, 'fr')} · ${searchTitle(country.entry, 'fr')}`,
    },
    summary: {
      en: 'Find related research and published surveillance series with Ask GIDS.',
      zh: '查看相关文献及已发布的监测曲线。',
      fr: 'Consultez les études liées et les séries de surveillance avec Ask GIDS.',
    },
    href: { en: path, zh: `/zh${path}`, fr: `/fr${path}` }, aliases: [queryValue],
  };
}

export function searchSiteEntries(entries: SiteSearchEntry[], query: string, locale: SearchLocale, limit: number): { entries: SiteSearchEntry[]; total: number } {
  const focused = compoundSearchEntry(entries, query);
  const ranked = entries.map(entry => ({ entry, rank: rankSiteSearch(entry, query, locale) }))
    .filter(item => item.rank > 0).sort((a, b) => b.rank - a.rank || searchTitle(a.entry, locale).localeCompare(searchTitle(b.entry, locale), locale));
  const all = [...(focused ? [focused] : []), ...ranked.map(item => item.entry)];
  return { entries: all.slice(0, limit), total: all.length };
}
