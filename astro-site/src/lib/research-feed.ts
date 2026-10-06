export type ResearchFeedScope =
  | 'diseases'
  | 'countries'
  | 'topics'
  | 'study-types'
  | 'peer-review'
  | 'collections';

export interface ResearchFeedArticle {
  slug?: unknown;
  title?: unknown;
  published_at?: unknown;
  updated_at?: unknown;
  journal?: unknown;
  study_type?: unknown;
  article_type?: unknown;
  peer_review_status?: unknown;
  editorial_status?: unknown;
  why_it_matters_en?: unknown;
  why_it_matters_zh?: unknown;
  why_it_matters_fr?: unknown;
  diseases?: Array<{ disease_id?: unknown; slug?: unknown; name_en?: unknown; name_zh?: unknown; name_fr?: unknown }>;
  countries?: Array<{ code?: unknown; slug?: unknown; name_en?: unknown; name_zh?: unknown; name_fr?: unknown }>;
  topics?: Array<{ name?: unknown; slug?: unknown }>;
}

export interface ResearchFeedData {
  last_updated?: unknown;
  articles?: ResearchFeedArticle[];
  preprints?: ResearchFeedArticle[];
  reviews_and_guidelines?: ResearchFeedArticle[];
  facets?: {
    diseases?: Array<{ disease_id?: unknown; slug?: unknown; name_en?: unknown; name_zh?: unknown; name_fr?: unknown }>;
    countries?: Array<{ code?: unknown; slug?: unknown; name_en?: unknown; name_zh?: unknown; name_fr?: unknown }>;
    topics?: Array<{ slug?: unknown; name?: unknown }>;
  };
}

export interface ResearchFeedDefinition {
  scope: ResearchFeedScope;
  value: string;
  label: string;
  labelZh?: string;
  labelFr?: string;
  path: string;
  count: number;
}

export type ResearchFeedLanguage = 'en' | 'zh' | 'fr';

const dynamicFeedPrefix = '/research/rss';
const peerReviewedValues = new Set(['peer-reviewed', 'peer-reviewed-article', 'peer-reviewed-paper']);
const preprintValues = new Set(['preprint', 'preprints']);

export function toResearchFeedSlug(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const slug = value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) ? slug : null;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function definitionPath(scope: ResearchFeedScope, value: string): string {
  return `${dynamicFeedPrefix}/${scope}/${value}.xml`;
}

function articleIdentity(article: ResearchFeedArticle): string {
  return text(article.slug) || text(article.title);
}

function normalizePeerReviewStatus(value: unknown): string | null {
  const slug = toResearchFeedSlug(value);
  if (slug === 'peer-reviewed') return 'peer-reviewed';
  if (preprintValues.has(slug ?? '')) return 'preprint';
  return slug;
}

function isReviewOrGuideline(article: ResearchFeedArticle): boolean {
  const studyType = toResearchFeedSlug(article.study_type) ?? '';
  const articleType = toResearchFeedSlug(article.article_type) ?? '';
  return studyType.includes('review')
    || studyType === 'meta-analysis'
    || studyType.includes('guideline')
    || studyType.includes('consensus')
    || articleType.includes('review')
    || articleType.includes('guideline')
    || articleType.includes('consensus');
}

export function filterResearchFeedArticles(
  data: ResearchFeedData,
  scope?: ResearchFeedScope,
  rawValue?: string,
): ResearchFeedArticle[] {
  const articles = (Array.isArray(data.articles) ? data.articles : []).filter(article => (
    toResearchFeedSlug(article.peer_review_status) === 'peer-reviewed'
    || article.editorial_status == null && article.peer_review_status == null
  ));
  if (!scope) return [...articles];
  const value = toResearchFeedSlug(rawValue);
  if (!value) return [];

  if (scope === 'peer-review' && preprintValues.has(value)) {
    return (data.preprints ?? []).filter(article => (
      toResearchFeedSlug(article.peer_review_status) === 'preprint'
      && toResearchFeedSlug(article.editorial_status) === 'published'
    ));
  }

  if (scope === 'collections' && value === 'reviews-and-guidelines') {
    const explicit = new Set(
      (data.reviews_and_guidelines ?? []).map(articleIdentity).filter(Boolean),
    );
    return articles.filter(article => explicit.has(articleIdentity(article)) || isReviewOrGuideline(article));
  }

  return articles.filter(article => {
    if (scope === 'diseases') {
      return (article.diseases ?? []).some(item => (
        toResearchFeedSlug(item.slug) === value
        || toResearchFeedSlug(item.disease_id) === value
      ));
    }
    if (scope === 'countries') {
      return (article.countries ?? []).some(item => (
        toResearchFeedSlug(item.slug) === value
        || toResearchFeedSlug(item.code) === value
      ));
    }
    if (scope === 'topics') {
      return (article.topics ?? []).some(item => (
        toResearchFeedSlug(item.slug ?? item.name) === value
      ));
    }
    if (scope === 'study-types') return toResearchFeedSlug(article.study_type) === value;
    if (scope === 'peer-review') {
      const requested = preprintValues.has(value) ? 'preprint' : (peerReviewedValues.has(value) ? 'peer-reviewed' : value);
      return normalizePeerReviewStatus(article.peer_review_status) === requested;
    }
    return false;
  });
}

export function buildResearchFeedDefinitions(data: ResearchFeedData): ResearchFeedDefinition[] {
  const definitions = new Map<string, Omit<ResearchFeedDefinition, 'count'>>();
  const add = (scope: ResearchFeedScope, rawValue: unknown, rawLabel: unknown, rawLabelZh?: unknown, rawLabelFr?: unknown) => {
    const value = toResearchFeedSlug(rawValue);
    const label = text(rawLabel);
    if (!value || !label) return;
    const path = definitionPath(scope, value);
    definitions.set(path, {
      scope,
      value,
      label,
      labelZh: text(rawLabelZh) || undefined,
      ...(text(rawLabelFr) ? { labelFr: text(rawLabelFr) } : {}),
      path,
    });
  };

  for (const facet of data.facets?.diseases ?? []) {
    add('diseases', facet.slug ?? facet.disease_id, facet.name_en ?? facet.slug, facet.name_zh, facet.name_fr);
  }
  for (const facet of data.facets?.countries ?? []) {
    add('countries', facet.slug ?? facet.code, facet.name_en ?? facet.code, facet.name_zh, facet.name_fr);
  }
  for (const facet of data.facets?.topics ?? []) {
    add('topics', facet.slug ?? facet.name, facet.name ?? facet.slug);
  }

  const studyTypes = new Map<string, string>();
  for (const article of data.articles ?? []) {
    const value = toResearchFeedSlug(article.study_type);
    const label = text(article.study_type);
    if (value && label && !studyTypes.has(value)) studyTypes.set(value, label);
  }
  for (const [value, label] of [...studyTypes].sort((left, right) => left[1].localeCompare(right[1]))) {
    add('study-types', value, label);
  }

  // Keep these high-value subscription URLs available even when a release contains no matches.
  add('collections', 'reviews-and-guidelines', 'Reviews and guidelines', '综述与指南', 'Revues et recommandations');
  add('peer-review', 'peer-reviewed', 'Peer-reviewed research', '同行评议研究', 'Recherche évaluée par les pairs');
  add('peer-review', 'preprint', 'Preprints', '预印本', 'Prépublications');

  return [...definitions.values()].map(definition => ({
    ...definition,
    count: filterResearchFeedArticles(data, definition.scope, definition.value).length,
  }));
}

export function findResearchFeedDefinition(
  data: ResearchFeedData,
  scope: unknown,
  value: unknown,
): ResearchFeedDefinition | null {
  if (typeof scope !== 'string' || typeof value !== 'string') return null;
  return buildResearchFeedDefinitions(data).find(definition => (
    definition.scope === scope && definition.value === value
  )) ?? null;
}

function escapeXml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function validDate(value: unknown): Date | null {
  if (typeof value !== 'string' && typeof value !== 'number' && !(value instanceof Date)) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function sortedArticles(articles: ResearchFeedArticle[]): ResearchFeedArticle[] {
  return [...articles].sort((left, right) => {
    const leftTime = validDate(left.published_at)?.getTime() ?? 0;
    const rightTime = validDate(right.published_at)?.getTime() ?? 0;
    return rightTime - leftTime || articleIdentity(left).localeCompare(articleIdentity(right));
  });
}

export function renderResearchFeedXml({
  data,
  definition,
  site,
  limit = 50,
  language = 'en',
}: {
  data: ResearchFeedData;
  definition?: ResearchFeedDefinition | null;
  site: URL | string;
  limit?: number;
  language?: ResearchFeedLanguage;
}): string {
  const origin = site instanceof URL ? site : new URL(site);
  const localePrefix = language === 'fr' ? '/fr' : language === 'zh' ? '/zh' : '';
  const feedPath = `${localePrefix}${definition?.path ?? '/research/rss.xml'}`;
  const label = language === 'fr' ? (definition?.labelFr || definition?.label || '') : language === 'zh' ? (definition?.labelZh || definition?.label || '') : definition?.label || '';
  const feedTitle = definition
    ? language === 'fr' ? `Radar de recherche GIDS · ${label}` : language === 'zh' ? `GIDS Research Radar · ${label}` : `GIDS Research Radar · ${label}`
    : language === 'fr' ? 'Radar de recherche GIDS' : 'GIDS Research Radar';
  const feedDescription = definition
    ? language === 'fr' ? `Publications sur les maladies infectieuses filtrées par ${label}.` : language === 'zh' ? `按${label}筛选的已发表传染病文献。` : `Published infectious-disease literature filtered to ${label}.`
    : language === 'fr' ? 'Métadonnées de la littérature sur les maladies infectieuses et résumés GIDS contrôlés par la qualité.' : language === 'zh' ? '传染病文献元数据及通过质量门禁的 GIDS 证据摘要。' : 'Published infectious-disease literature metadata and quality-gated GIDS evidence summaries.';
  const articles = sortedArticles(filterResearchFeedArticles(data, definition?.scope, definition?.value))
    .slice(0, Math.max(0, limit));
  const items = articles.map(article => {
    const slug = text(article.slug);
    if (!slug) return '';
    const url = new URL(`${localePrefix}/research/articles/${slug}/`, origin).toString();
    const diseaseName = (item: NonNullable<ResearchFeedArticle['diseases']>[number]) => language === 'fr'
      ? text(item.name_fr) || text(item.name_en)
      : language === 'zh' ? text(item.name_zh) || text(item.name_en) : text(item.name_en);
    const diseases = (article.diseases ?? []).map(diseaseName).filter(Boolean);
    const description = (language === 'fr' ? text(article.why_it_matters_fr) : language === 'zh' ? text(article.why_it_matters_zh) : text(article.why_it_matters_en))
      || (language === 'fr' ? `Notice Research Radar liée à ${diseases.join(', ') || 'une maladie infectieuse'}.` : language === 'zh' ? `与${diseases.join('、') || '传染病'}相关的 Research Radar 记录。` : `A Research Radar record related to ${diseases.join(', ') || 'infectious disease'}.`);
    const publishedAt = validDate(article.published_at);
    const categories = [
      ...(article.diseases ?? []).map(diseaseName),
      ...(article.topics ?? []).map(item => text(item.name)),
      text(article.study_type),
    ].filter(Boolean).map(category => `    <category>${escapeXml(category)}</category>`).join('\n');
    return `  <item>
    <title>${escapeXml(text(article.title) || 'Untitled research record')}</title>
    <link>${escapeXml(url)}</link>
    <guid isPermaLink="true">${escapeXml(url)}</guid>
${publishedAt ? `    <pubDate>${escapeXml(publishedAt.toUTCString())}</pubDate>\n` : ''}    <description>${escapeXml(description)}</description>
${categories ? `${categories}\n` : ''}  </item>`;
  }).filter(Boolean).join('\n');
  const lastBuildDate = validDate(data.last_updated);

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>${escapeXml(feedTitle)}</title>
  <link>${escapeXml(new URL(`${localePrefix}/research/`, origin).toString())}</link>
  <description>${escapeXml(feedDescription)}</description>
  <language>${language}</language>
  <atom:link href="${escapeXml(new URL(feedPath, origin).toString())}" rel="self" type="application/rss+xml" />
${lastBuildDate ? `  <lastBuildDate>${escapeXml(lastBuildDate.toUTCString())}</lastBuildDate>\n` : ''}${items}
</channel>
</rss>
`;
}
