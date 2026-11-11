export type LiteratureLocale = 'en' | 'zh' | 'fr';
export interface LiteratureFilters { query: string; country: string; study: string; topic: string; from: string; to: string; access: string; evidence: string; publication: string; sort: string; }
export const emptyLiteratureFilters: LiteratureFilters = { query: '', country: '', study: '', topic: '', from: '', to: '', access: '', evidence: '', publication: '', sort: 'newest' };
const normalized = (value: unknown) => String(value ?? '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
export const literaturePageSize = 24;
export const literatureRecordId = (article: any): string => String(article.literature_id || article.article_id || article.doi || article.pmid || article.slug || '');
const searchableText = (article: any) => normalized([article.title, article.title_zh, article.title_fr, article.journal, article.doi, article.pmid, ...(article.authors ?? []), ...(article.countries ?? []).flatMap((x: any) => [x.name_en, x.name_zh, x.name_fr]), ...(article.topics ?? []).map((x: any) => x.name)].join(' '));

/** Small filter projection. Full cards stay in immutable, separately loaded pages. */
export function literatureIndexRecord(article: any, shard: string) {
  return { literature_id: literatureRecordId(article), shard, search_text: searchableText(article), title_sort: article.title || '', published_at: article.published_at,
    country_codes: (article.countries ?? []).map((country: any) => String(country.code).toUpperCase()), study_type: article.study_type,
    topic_names: (article.topics ?? []).map((topic: any) => topic.name), open_access_status: article.open_access_status,
    detail_available: article.detail_available, peer_review_status: article.peer_review_status };
}

export function literatureYearRangeError(filters: Pick<LiteratureFilters, 'from' | 'to'>): 'invalid' | 'reversed' | null {
  if ([filters.from, filters.to].some(year => year && !/^(18|19|20|21)\d{2}$/.test(year))) return 'invalid';
  return filters.from && filters.to && filters.from > filters.to ? 'reversed' : null;
}

/** A list projection deliberately excludes full summaries and knowledge graphs. */
export function literatureArticle(article: any) {
  const { article_id, slug, title, title_zh, title_fr, doi, pmid, journal, study_type, published_at, open_access_status, open_access_url, detail_available, discovery_score, peer_review_status } = article;
  return { article_id, slug, title, title_zh, title_fr, doi, pmid, journal, study_type, published_at, open_access_status, open_access_url, detail_available, discovery_score, peer_review_status,
    authors: article.authors ?? [], diseases: article.diseases ?? [], countries: article.countries ?? [], topics: article.topics ?? [],
  };
}

export function filterLiterature(articles: any[], filters: LiteratureFilters): any[] {
  if (literatureYearRangeError(filters)) return [];
  const words = normalized(filters.query).split(/\s+/).filter(Boolean);
  const matches = articles.filter(article => {
    const year = String(article.published_at ?? '').slice(0, 4);
    const haystack = article.search_text ?? searchableText(article);
    return words.every(word => haystack.includes(word))
      && (!filters.country || (article.country_codes ?? (article.countries ?? []).map((x: any) => String(x.code).toUpperCase())).includes(filters.country.toUpperCase()))
      && (!filters.study || article.study_type === filters.study)
      && (!filters.topic || (article.topic_names ?? (article.topics ?? []).map((x: any) => x.name)).includes(filters.topic))
      && (!filters.from || Boolean(year && year >= filters.from))
      && (!filters.to || Boolean(year && year <= filters.to))
      && (!filters.access || (filters.access === 'open' ? article.open_access_status === 'open' : article.open_access_status !== 'open'))
      && (!filters.evidence || (filters.evidence === 'summary' ? article.detail_available !== false : article.detail_available === false));
  }).filter(article => !filters.publication || article.peer_review_status === filters.publication);
  return matches.sort((a, b) => filters.sort === 'oldest'
    ? String(a.published_at ?? '9999').localeCompare(String(b.published_at ?? '9999')) || String(a.title_sort ?? a.title).localeCompare(String(b.title_sort ?? b.title))
    : filters.sort === 'title' ? String(a.title_sort ?? a.title).localeCompare(String(b.title_sort ?? b.title))
    : String(b.published_at ?? '').localeCompare(String(a.published_at ?? '')) || String(a.title_sort ?? a.title).localeCompare(String(b.title_sort ?? b.title)));
}

export function literaturePage(records: any[], requested: number, size = literaturePageSize) {
  const totalPages = Math.max(1, Math.ceil(records.length / size));
  const page = Math.min(totalPages, Math.max(1, Number.isSafeInteger(requested) ? requested : 1));
  return { page, totalPages, records: records.slice((page - 1) * size, page * size) };
}

export function literatureTrend(articles: any[]): Array<{ year: string; count: number }> {
  const years = new Map<string, number>();
  for (const article of articles) {
    const year = String(article.published_at ?? '').slice(0, 4);
    if (/^(18|19|20|21)\d{2}$/.test(year)) years.set(year, (years.get(year) ?? 0) + 1);
  }
  return [...years].sort(([a], [b]) => a.localeCompare(b)).map(([year, count]) => ({ year, count }));
}

const QUERY_ALIASES: Record<string, string[]> = {
  influenza: ['influenza', 'flu'], 'covid-19': ['COVID-19', 'SARS-CoV-2'], tuberculosis: ['tuberculosis', 'Mycobacterium tuberculosis'],
  mpox: ['mpox', 'monkeypox'], pertussis: ['pertussis', 'whooping cough'], dengue: ['dengue', 'dengue fever'],
  'hiv-infection': ['HIV', 'human immunodeficiency virus'],
};
/** Explicit title/abstract terms; do not invent or imply validated MeSH mappings. */
export function diseaseLiteratureSearchStrategy(disease: { slug?: string; name_en?: string; en?: string }) {
  const terms = [...new Set([disease.name_en || disease.en, ...(QUERY_ALIASES[disease.slug ?? ''] ?? [])].filter((x): x is string => Boolean(x)).map(x => x.replace(/["\[\]():]/g, ' ').replace(/\s+/g, ' ').trim()))];
  const pubmed = `(${terms.map(term => `"${term}"[Title/Abstract]`).join(' OR ')})`;
  const europepmc = `(${terms.map(term => `TITLE_ABS:"${term}"`).join(' OR ')})`;
  const scholar = terms.map(term => `"${term}"`).join(' OR ');
  return { terms, pubmed, europepmc, scholar,
    pubmedUrl: `https://pubmed.ncbi.nlm.nih.gov/?term=${encodeURIComponent(pubmed)}`,
    europepmcUrl: `https://europepmc.org/search?query=${encodeURIComponent(europepmc)}`,
    scholarUrl: `https://scholar.google.com/scholar?q=${encodeURIComponent(scholar)}`,
  };
}
