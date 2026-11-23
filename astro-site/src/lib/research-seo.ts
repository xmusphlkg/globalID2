import { absoluteSiteUrl } from './papers.ts';
import { normalizeSeoText, type SeoLocale } from './seo.ts';

/** Derive hub links without adding fields to immutable literature card shards. */
export function researchPublicationWeek(article: { publication_week?: string; published_at?: string | null }): string | undefined {
  if (/^\d{4}-W(?:0[1-9]|[1-4]\d|5[0-3])$/.test(article.publication_week ?? '')) return article.publication_week;
  const day = String(article.published_at ?? '').slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return undefined;
  const date = new Date(`${day}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== day) return undefined;
  date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7));
  const year = date.getUTCFullYear();
  const week = Math.ceil(((date.getTime() - Date.UTC(year, 0, 1)) / 86400000 + 1) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}

/** Describe only the records rendered on the initial page, including source-only records. */
export function buildResearchItemList(articles: any[], siteOrigin: string | URL, locale: SeoLocale, limit: number) {
  const prefix = locale === 'en' ? '' : `/${locale}`;
  return {
    '@type': 'ItemList',
    numberOfItems: articles.length,
    itemListElement: articles.slice(0, limit).flatMap((article, index) => {
      const href = article.detail_available !== false && article.slug
        ? `${prefix}/research/articles/${article.slug}/`
        : article.doi ? `https://doi.org/${article.doi}`
          : article.pmid ? `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(article.pmid)}/`
            : article.open_access_url;
      if (!href) return [];
      return [{ '@type': 'ListItem', position: index + 1, name: normalizeSeoText(article[`title_${locale}`] || article.title), url: absoluteSiteUrl(href, siteOrigin).toString() }];
    }),
  };
}

/** Catalogue coverage, not a claim about research quality or disease incidence. */
export function researchCoverage(articles: any[]) {
  const years = articles.map(article => String(article.published_at ?? '').slice(0, 4)).filter(year => /^(18|19|20|21)\d{2}$/.test(year)).sort();
  const topics = new Map<string, number>();
  for (const article of articles) {
    for (const name of new Set<string>((article.topics ?? []).map((topic: any) => normalizeSeoText(topic.name)).filter(Boolean))) {
      topics.set(name, (topics.get(name) ?? 0) + 1);
    }
  }
  return {
    startYear: years[0], endYear: years.at(-1),
    topics: [...topics].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])),
  };
}
