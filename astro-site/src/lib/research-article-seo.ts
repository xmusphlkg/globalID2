import { clampSeoDescription, clampSeoTitle, normalizeSeoText } from './seo-text.ts';

/** Shared by Astro previews and the production Papers projection renderer. */
export function buildResearchArticleSeo(article: any, locale: 'en' | 'zh' | 'fr') {
  const summary = article.summary?.[locale] ?? {};
  const suffix = locale === 'zh' ? ' | GIDS 中文证据摘要' : locale === 'fr' ? ' | GIDS — résumé français' : ' | GIDS';
  const doiSuffix = article.doi ? String(article.doi).split('/').filter(Boolean).at(-1) : '';
  const identifier = doiSuffix && [...doiSuffix].length <= 18 ? doiSuffix : article.pmid ? `PMID ${article.pmid}` : String(article.slug).slice(-8);
  const disambiguator = ` · ${identifier}`;
  const context = locale === 'zh' ? '本页提供研究设计、主要发现、重要局限、来源标识及相关监测背景。'
    : locale === 'fr' ? 'Consultez le plan de l’étude, les résultats principaux, les limites, les identifiants de source et le contexte de surveillance associé.'
      : 'Review the study design, main findings, important limitations, source identifiers, and related surveillance context.';
  const relevance = normalizeSeoText(article[`why_it_matters_${locale}`]) || normalizeSeoText(summary.public_health_relevance) || normalizeSeoText(summary.main_findings);
  const abstract = ['research_question', 'study_design', 'main_findings', 'limitations'].map(key => normalizeSeoText(summary[key])).filter(Boolean).join(' ');
  return {
    title: `${clampSeoTitle(article.title, 70 - suffix.length - disambiguator.length)}${disambiguator}${suffix}`,
    description: clampSeoDescription(`${relevance} ${context}`),
    abstract: abstract ? `${locale === 'zh' ? 'GIDS 证据摘要' : locale === 'fr' ? 'Synthèse des preuves GIDS' : 'GIDS evidence summary'}: ${abstract}` : undefined,
    identifier: article.doi ? { '@type': 'PropertyValue', propertyID: 'DOI', value: article.doi, url: `https://doi.org/${article.doi}` } : article.pmid ? `PMID:${article.pmid}` : article.article_id,
  };
}
