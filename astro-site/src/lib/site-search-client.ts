import { mainSiteBase, siteHref } from './papers.ts';
import type { SiteSearchEntry } from './site-search.ts';

/** Keep the quick-search dialog and full results on the same published indexes. */
export async function loadSiteSearchEntries(includeResearch: boolean, fetcher: typeof fetch = fetch): Promise<SiteSearchEntry[]> {
  const base = mainSiteBase();
  const readIndex = async (url: string): Promise<SiteSearchEntry[]> => {
    const response = await fetcher(url);
    if (!response.ok) throw new Error('Search index unavailable');
    const payload = await response.json();
    if (!Array.isArray(payload.entries)) throw new Error('Invalid search index');
    return payload.entries;
  };
  const researchPath = includeResearch ? siteHref('/research/search-index.json') : '';
  const [entries, research] = await Promise.all([
    readIndex(`${base}/site-data/search-index.json`),
    researchPath ? readIndex(researchPath.startsWith('/') ? `${base}${researchPath}` : researchPath) : undefined,
  ]);
  return research ? [...entries.filter(entry => entry.kind !== 'research'), ...research] : entries;
}
