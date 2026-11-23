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

export function researchDiseaseOverview(input: { name: string; articles: any[]; locale: SeoLocale }): string {
  const { name, articles, locale } = input;
  const coverage = researchCoverage(articles);
  const summaryCount = articles.filter(article => article.detail_available !== false).length;
  const countries = new Set(articles.flatMap(article => (article.countries ?? []).map((country: any) => country.code)).filter(Boolean)).size;
  const surveillanceLinks = articles.reduce((count, article) => count + (Array.isArray(article.related_surveillance) ? article.related_surveillance.length : 0), 0);
  const studies = new Map<string, number>();
  for (const article of articles) {
    const study = normalizeSeoText(article.study_type);
    if (study) studies.set(study, (studies.get(study) ?? 0) + 1);
  }
  const topTopics = coverage.topics.slice(0, 3).map(([topic]) => topic);
  const topStudies = [...studies].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 2).map(([study]) => study);
  const yearRange = coverage.startYear ? `${coverage.startYear}${coverage.endYear && coverage.endYear !== coverage.startYear ? `–${coverage.endYear}` : ''}` : 'the available publication years';
  if (locale === 'zh') {
    return `${name}研究专题覆盖${yearRange}，目录收录${articles.length}篇文献，其中${summaryCount}篇提供结构化证据摘要。当前较集中的研究主题包括${topTopics.length ? topTopics.join('、') : '多个公共卫生主题'}；常见研究类型为${topStudies.length ? topStudies.join('、') : '多种观察性和实验性研究'}。文献关联${countries}个地理区域，并提供${surveillanceLinks}条官方监测背景链接，便于把研究发表活动与公共卫生报告放在同一语境中比较。目录数量只表示本站覆盖情况，不能代表疾病发病水平或研究共识。阅读单篇页面时，应同时查看研究设计、主要发现、局限和来源。当前证据缺口仍需结合原始研究和监测数据判断，尤其是不同地区、时间段和人群之间的可比性。`;
  }
  if (locale === 'fr') {
    return `La littérature sur ${name} couvre ${yearRange} dans le catalogue GIDS, avec ${articles.length} publications indexées, dont ${summaryCount} synthèses structurées. Les thèmes les plus représentés sont ${topTopics.length ? topTopics.join(', ') : 'plusieurs thèmes de santé publique'} ; les types d’études fréquents comprennent ${topStudies.length ? topStudies.join(' et ') : 'plusieurs approches observationnelles et expérimentales'}. Les fiches relient ${countries} zones géographiques et ${surveillanceLinks} éléments de contexte de surveillance officielle, afin de comparer l’activité éditoriale aux rapports de santé publique. Ces volumes décrivent la couverture du catalogue et ne mesurent ni l’incidence ni un consensus scientifique. Consultez pour chaque article le plan d’étude, les résultats, les limites et la source originale. Les lacunes restantes doivent être interprétées à partir des études primaires et des données de surveillance, notamment pour comparer les populations, les périodes et les régions.`;
  }
  return `${name} research in the GIDS catalogue spans ${yearRange} and includes ${articles.length} indexed publications, of which ${summaryCount} have structured evidence summaries. The most represented themes are ${topTopics.length ? topTopics.join(', ') : 'several public-health topics'}, while ${topStudies.length ? topStudies.join(' and ') : 'multiple observational and experimental designs'} appear most often. Records connect to ${countries} geographic areas and ${surveillanceLinks} official surveillance context links, allowing publication activity to be read alongside public-health reporting. These counts describe catalogue coverage; they do not measure disease incidence or establish a research consensus. Each article should be read with its study design, findings, limitations and original source. Remaining evidence gaps require primary studies and surveillance data, especially when comparing populations, periods and regions.`;
}
