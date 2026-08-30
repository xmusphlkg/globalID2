import {
  isIndexableResearchCollection,
  isIndexableDisease,
  toSeoSlug,
} from './seo.ts';
import {
  hasCountryDataSnapshot,
  type CountryDataSnapshotMeta,
} from './country-coverage.ts';
import {
  buildPublishableReports,
  normalizeReportRouteSegment,
  reportArchiveCountries,
  type ReportIndexEntry,
} from './report-routes.ts';

export interface SitemapEntry {
  path: string;
  lastmod?: string;
  alternates?: Array<{ locale: 'en' | 'zh-CN' | 'x-default'; path: string }>;
}

export interface SitemapMeta {
  generated_at?: unknown;
  countries?: Array<CountryDataSnapshotMeta & { code?: unknown }>;
}

export interface SitemapDisease {
  disease_id?: unknown;
  slug?: unknown;
}

export interface SitemapReport extends ReportIndexEntry {
  id?: unknown;
  created_at?: unknown;
}

export interface BuildSitemapEntriesOptions {
  meta: SitemapMeta;
  diseases: unknown;
  reports: unknown;
  loadReport: (id: string) => unknown | null;
  loadDisease?: (id: string) => unknown | null;
  loadKnowledge?: (id: string) => unknown | null;
  loadCountry?: (code: string) => unknown | null;
  countryDiseaseLimit?: number;
  situation?: {
    latest?: { public_enabled?: unknown; generated_at?: unknown; content_updated_at?: unknown; report?: { as_of?: unknown } } | null;
    weeks?: Array<{ period_key?: unknown; iso_week?: unknown; generated_at?: unknown; content_updated_at?: unknown; report?: { period_key?: unknown; as_of?: unknown } }>;
    months?: Array<{ period_key?: unknown; generated_at?: unknown; content_updated_at?: unknown; report?: { period_key?: unknown; as_of?: unknown } }>;
    archives?: Array<{ period_key?: unknown; iso_week?: unknown; generated_at?: unknown; content_updated_at?: unknown; report?: { period_key?: unknown; as_of?: unknown } }>;
  };
  research?: {
    last_updated?: unknown;
    articles?: Array<{
      slug?: unknown;
      title?: unknown;
      updated_at?: unknown;
      published_at?: unknown;
      study_type?: unknown;
      article_type?: unknown;
      peer_review_status?: unknown;
      editorial_status?: unknown;
      indexable?: unknown;
      diseases?: Array<{ disease_id?: unknown; slug?: unknown; name_en?: unknown }>;
      countries?: Array<{ code?: unknown; slug?: unknown; name_en?: unknown }>;
      topics?: Array<{ name?: unknown; slug?: unknown }>;
    }>;
    preprints?: Array<{
      slug?: unknown;
      title?: unknown;
      updated_at?: unknown;
      published_at?: unknown;
      study_type?: unknown;
      article_type?: unknown;
      peer_review_status?: unknown;
      editorial_status?: unknown;
      indexable?: unknown;
      diseases?: Array<{ disease_id?: unknown; slug?: unknown; name_en?: unknown }>;
      countries?: Array<{ code?: unknown; slug?: unknown; name_en?: unknown }>;
      topics?: Array<{ name?: unknown; slug?: unknown }>;
    }>;
    reviews_and_guidelines?: Array<{ slug?: unknown; title?: unknown; study_type?: unknown; article_type?: unknown }>;
    facets?: {
      diseases?: Array<{ disease_id?: unknown; slug?: unknown; name_en?: unknown; name_zh?: unknown; count?: unknown }>;
      countries?: Array<{ slug?: unknown; code?: unknown; name_en?: unknown; name_zh?: unknown; count?: unknown }>;
      topics?: Array<{ slug?: unknown; name?: unknown; count?: unknown }>;
      weeks?: Array<{ week?: unknown; article_count?: unknown; start_date?: unknown; end_date?: unknown }>;
    };
  };
}

export const STATIC_SITEMAP_PATHS = [
  '/',
  '/about/',
  '/changelog/',
  '/subscribe/',
  '/copyright/',
  '/terms/',
  '/countries/',
  '/diseases/',
  '/downloads/',
] as const;

export const SITEMAP_GROUPS = [
  'static',
  'diseases',
  'countries',
  'reports',
  'country-diseases',
  'situation',
  'research',
] as const;

export type SitemapGroup = typeof SITEMAP_GROUPS[number];

export function normalizeSitemapDate(value: unknown): string | undefined {
  if (typeof value !== 'string' && typeof value !== 'number' && !(value instanceof Date)) {
    return undefined;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function pathSegment(value: unknown, lowercase = false): string | null {
  return normalizeReportRouteSegment(value, lowercase);
}

function localizedEntry(path: string, lastmod?: string): SitemapEntry[] {
  const englishPath = path;
  const chinesePath = path === '/' ? '/zh/' : `/zh${path}`;
  const alternates = [
    { locale: 'en' as const, path: englishPath },
    { locale: 'zh-CN' as const, path: chinesePath },
    { locale: 'x-default' as const, path: englishPath },
  ];
  return [
    { path: englishPath, lastmod, alternates },
    { path: chinesePath, lastmod, alternates },
  ];
}

function diseaseIsIndexable(
  disease: SitemapDisease,
  loadDisease?: (id: string) => unknown | null,
  loadKnowledge?: (id: string) => unknown | null,
): boolean {
  const id = typeof disease.disease_id === 'string' ? disease.disease_id.toLowerCase() : '';
  if (!id || !loadDisease || !loadKnowledge) return true;
  const data = loadDisease(id) as Record<string, any> | null;
  const knowledge = loadKnowledge(id) as Record<string, any> | null;
  return isIndexableDisease({
    countrySeries: data?.country_series,
    knowledgeStatus: knowledge?.knowledge_status ?? data?.knowledge_status,
    knowledgeSources: knowledge?.knowledge_sources ?? data?.knowledge_sources,
  });
}

function countryDiseaseEntries(
  meta: SitemapMeta,
  loadCountry?: (code: string) => unknown | null,
  limit = 50,
): SitemapEntry[] {
  if (!loadCountry) return [];
  const candidates: Array<{ path: string; lastmod?: string; score: number }> = [];
  for (const country of meta.countries ?? []) {
    const code = pathSegment(country.code, true);
    if (!code || !hasCountryDataSnapshot(country)) continue;
    const data = loadCountry(code) as Record<string, any> | null;
    const sourceUrls = [
      data?.source_info?.primary_url,
      ...(data?.source_info?.sources ?? []).map((source: any) => source?.url),
    ].filter((url): url is string => typeof url === 'string' && url.length > 0);
    if (!data || sourceUrls.length === 0) continue;
    for (const series of Object.values(data.disease_series ?? {}) as Array<Record<string, any>>) {
      const dates = Array.isArray(series.dates) ? series.dates : [];
      const first = dates[0];
      const last = dates.at(-1);
      const spanDays = first && last ? (new Date(last).getTime() - new Date(first).getTime()) / 86_400_000 : 0;
      const granularity = String(series.period_granularity ?? '').toLowerCase();
      const seriesIdentity = Number(series.available_series_count ?? 0) > 0
        || (Array.isArray(series.selected_series_codes) && series.selected_series_codes.length > 0)
        || (Array.isArray(series.source_series) && series.source_series.length > 0);
      const enoughHistory = ['annual', 'yearly'].includes(granularity)
        ? dates.length >= 5
        : dates.length >= 24 && spanDays >= 365;
      const slug = toSeoSlug(series.slug);
      if (!seriesIdentity || !enoughHistory || !slug) continue;
      candidates.push({
        path: `/countries/${code}/diseases/${slug}/`,
        lastmod: normalizeSitemapDate(data.generated_at),
        score: Number(series.total_cases ?? 0) + dates.length,
      });
    }
  }
  return candidates
    .sort((left, right) => right.score - left.score || left.path.localeCompare(right.path))
    .slice(0, Math.max(0, limit))
    .flatMap(entry => localizedEntry(entry.path, entry.lastmod));
}

export function buildSitemapGroups({
  meta,
  diseases,
  reports,
  loadReport,
  loadDisease,
  loadKnowledge,
  loadCountry,
  countryDiseaseLimit = 50,
  situation,
  research,
}: BuildSitemapEntriesOptions): Record<SitemapGroup, SitemapEntry[]> {
  const siteLastmod = normalizeSitemapDate(meta.generated_at);
  const groups: Record<SitemapGroup, SitemapEntry[]> = {
    // Static legal/navigation pages do not necessarily change with the data
    // snapshot. Omitting lastmod is more accurate than refreshing it on every
    // data release.
    static: STATIC_SITEMAP_PATHS.flatMap(path => localizedEntry(path)),
    diseases: [],
    countries: [],
    reports: [],
    'country-diseases': countryDiseaseEntries(meta, loadCountry, countryDiseaseLimit),
    situation: [],
    research: [],
  };

  if (research) {
    const researchLastmod = normalizeSitemapDate(research.last_updated) ?? siteLastmod;
    const publicPreprints = (research.preprints ?? []).filter(article => (
      article.peer_review_status === 'preprint' && article.editorial_status === 'published'
    ));
    const publicResearchArticles = [...(research.articles ?? []), ...publicPreprints]
      .filter(article => article.indexable !== false);
    const latestMatchingResearchUpdate = (
      predicate: (article: (typeof publicResearchArticles)[number]) => boolean,
    ) => {
      const values = publicResearchArticles
        .filter(predicate)
        .map(article => normalizeSitemapDate(article.updated_at ?? article.published_at))
        .filter((value): value is string => Boolean(value))
        .sort();
      return values.at(-1) ?? researchLastmod;
    };
    groups.research.push(...localizedEntry('/research/', researchLastmod));
    groups.research.push(...localizedEntry('/research/ask/', researchLastmod));
    groups.research.push(...localizedEntry('/research/graph/', researchLastmod));
    groups.research.push(...localizedEntry('/research/integrity/', researchLastmod));
    if (isIndexableResearchCollection(publicPreprints.length)) {
      groups.research.push(...localizedEntry('/research/preprints/', researchLastmod));
    }
    for (const article of publicResearchArticles) {
      const slug = pathSegment(article.slug, true);
      if (!slug) continue;
      groups.research.push(...localizedEntry(`/research/articles/${slug}/`, normalizeSitemapDate(article.updated_at ?? article.published_at) ?? researchLastmod));
    }
    for (const disease of research.facets?.diseases ?? []) {
      const slug = pathSegment(disease.slug, true);
      const count = Number(disease.count ?? 0);
      if (slug && isIndexableResearchCollection(count)) groups.research.push(...localizedEntry(
        `/research/diseases/${slug}/`,
        latestMatchingResearchUpdate(article => (article.diseases ?? []).some(item => (
          pathSegment(item.slug, true) === slug
          || String(item.disease_id ?? '').toLowerCase() === String(disease.disease_id ?? '').toLowerCase()
        ))),
      ));
    }
    for (const country of research.facets?.countries ?? []) {
      const slug = pathSegment(country.slug ?? country.code, true);
      const count = Number(country.count ?? 0);
      if (slug && isIndexableResearchCollection(count)) groups.research.push(...localizedEntry(
        `/research/countries/${slug}/`,
        latestMatchingResearchUpdate(article => (article.countries ?? []).some(item => (
          pathSegment(item.slug ?? item.code, true) === slug
        ))),
      ));
    }
    for (const topic of research.facets?.topics ?? []) {
      const slug = pathSegment(topic.slug, true);
      const count = Number(topic.count ?? 0);
      if (slug && isIndexableResearchCollection(count)) groups.research.push(...localizedEntry(
        `/research/topics/${slug}/`,
        latestMatchingResearchUpdate(article => (article.topics ?? []).some(item => (
          pathSegment(item.slug ?? item.name, true) === slug
        ))),
      ));
    }
    for (const brief of research.facets?.weeks ?? []) {
      const week = typeof brief.week === 'string' && /^\d{4}-W\d{2}$/.test(brief.week) ? brief.week : null;
      const count = Number(brief.article_count ?? 0);
      const start = typeof brief.start_date === 'string' ? brief.start_date : null;
      const end = typeof brief.end_date === 'string' ? brief.end_date : null;
      if (week && isIndexableResearchCollection(count)) groups.research.push(...localizedEntry(
        `/research/weekly/${week}/`,
        latestMatchingResearchUpdate(article => {
          const published = typeof article.published_at === 'string' ? article.published_at.slice(0, 10) : '';
          return Boolean(published && start && end && published >= start && published <= end);
        }),
      ));
    }
  }

  if (situation?.latest?.public_enabled === true) {
    const lastmod = normalizeSitemapDate(situation.latest.report?.as_of ?? situation.latest.content_updated_at ?? situation.latest.generated_at) ?? siteLastmod;
    groups.situation.push(...localizedEntry('/situation/', lastmod));
    groups.situation.push(...localizedEntry('/situation/methodology/', lastmod));
    if ((situation.weeks ?? situation.archives ?? []).length > 0) {
      groups.situation.push(...localizedEntry('/situation/weekly/', lastmod));
    }
    if ((situation.months ?? []).length > 0) {
      groups.situation.push(...localizedEntry('/situation/monthly/', lastmod));
    }
    for (const archive of situation.weeks ?? situation.archives ?? []) {
      const rawWeek = archive.report?.period_key ?? archive.period_key ?? archive.iso_week;
      const week = typeof rawWeek === 'string' && /^\d{4}-W\d{2}$/.test(rawWeek)
        ? rawWeek
        : null;
      if (week) groups.situation.push(...localizedEntry(`/situation/weekly/${week}/`, normalizeSitemapDate(archive.report?.as_of ?? archive.content_updated_at ?? archive.generated_at) ?? lastmod));
    }
    for (const archive of situation.months ?? []) {
      const rawMonth = archive.report?.period_key ?? archive.period_key;
      const month = typeof rawMonth === 'string' && /^\d{4}-\d{2}$/.test(rawMonth)
        ? rawMonth
        : null;
      if (month) groups.situation.push(...localizedEntry(`/situation/monthly/${month}/`, normalizeSitemapDate(archive.report?.as_of ?? archive.content_updated_at ?? archive.generated_at) ?? lastmod));
    }
  }

  if (Array.isArray(diseases)) {
    for (const disease of diseases as SitemapDisease[]) {
      const slug = toSeoSlug(disease?.slug);
      if (!slug || !diseaseIsIndexable(disease, loadDisease, loadKnowledge)) continue;
      const id = typeof disease.disease_id === 'string' ? disease.disease_id.toLowerCase() : '';
      const data = id && loadDisease ? loadDisease(id) as Record<string, any> | null : null;
      const knowledge = id && loadKnowledge ? loadKnowledge(id) as Record<string, any> | null : null;
      const lastmod = normalizeSitemapDate(data?.generated_at ?? knowledge?.knowledge_updated_at) ?? siteLastmod;
      groups.diseases.push(...localizedEntry(`/diseases/${slug}/`, lastmod));
    }
  }

  for (const country of meta.countries ?? []) {
    const code = pathSegment(country.code, true);
    if (!code || !hasCountryDataSnapshot(country)) continue;
    const data = loadCountry?.(code) as Record<string, any> | null;
    groups.countries.push(...localizedEntry(
      `/countries/${code}/`,
      normalizeSitemapDate(data?.generated_at) ?? siteLastmod,
    ));
  }

  const publishableReports = buildPublishableReports(reports, loadReport);
  for (const country of reportArchiveCountries(publishableReports)) {
    groups.reports.push(...localizedEntry(`/countries/${country}/reports/`, siteLastmod));
  }
  for (const report of publishableReports) {
    const lastmod = normalizeSitemapDate(report.summary.created_at)
      ?? normalizeSitemapDate(report.detail.created_at)
      ?? siteLastmod;
    groups.reports.push(...localizedEntry(`/countries/${report.country}/reports/${report.id}/`, lastmod));
    for (const slug of report.diseaseSlugs) {
      groups.reports.push(...localizedEntry(`/countries/${report.country}/reports/${report.id}/${toSeoSlug(slug)}/`, lastmod));
    }
  }

  for (const group of SITEMAP_GROUPS) {
    const unique = new Map<string, SitemapEntry>();
    for (const entry of groups[group]) {
      if (!unique.has(entry.path)) unique.set(entry.path, entry);
    }
    groups[group] = [...unique.values()];
  }
  return groups;
}

export function buildSitemapEntries(options: BuildSitemapEntriesOptions): SitemapEntry[] {
  return Object.values(buildSitemapGroups(options)).flat();
}

function escapeXml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

export function renderSitemapXml(entries: SitemapEntry[], site: URL | string): string {
  const siteUrl = site instanceof URL ? site : new URL(site);
  const hasAlternates = entries.some(entry => entry.alternates?.length);
  const namespace = hasAlternates
    ? ' xmlns:xhtml="http://www.w3.org/1999/xhtml"'
    : '';
  const body = entries.map(({ path, lastmod, alternates }) => {
    const loc = new URL(path, siteUrl).toString();
    const lastmodTag = lastmod ? `\n    <lastmod>${escapeXml(lastmod)}</lastmod>` : '';
    const alternateTags = (alternates ?? []).map(alternate => (
      `\n    <xhtml:link rel="alternate" hreflang="${alternate.locale}" href="${escapeXml(new URL(alternate.path, siteUrl).toString())}" />`
    )).join('');
    return `  <url>\n    <loc>${escapeXml(loc)}</loc>${lastmodTag}${alternateTags}\n  </url>`;
  }).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"${namespace}>\n${body}\n</urlset>\n`;
}

export function renderSitemapIndexXml(groups: SitemapGroup[], site: URL | string): string {
  const siteUrl = site instanceof URL ? site : new URL(site);
  const body = groups.map(group => (
    `  <sitemap>\n    <loc>${escapeXml(new URL(`/sitemaps/${group}.xml`, siteUrl).toString())}</loc>\n  </sitemap>`
  )).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</sitemapindex>\n`;
}
