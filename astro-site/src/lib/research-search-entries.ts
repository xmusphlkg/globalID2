import { articleHref } from './papers.ts';

const clean = (values: unknown[]) => [...new Set(values.filter((value): value is string =>
  typeof value === 'string' && value.trim().length > 0).map(value => value.trim()))];

export function researchSearchEntries(research: any) {
  const entries = [];
  for (const article of [...(research.articles ?? []), ...(research.preprints ?? [])]) {
    if (article.indexable === false || !article.slug) continue;
    entries.push({
      id: `research:${article.article_id ?? article.slug}`, kind: 'research' as const,
      href: { en: articleHref(article.slug, 'en'), zh: articleHref(article.slug, 'zh'), fr: articleHref(article.slug, 'fr') },
      title: { en: article.title, zh: article.title_zh, fr: article.title_fr ?? article.title },
      summary: {
        en: article.why_it_matters_en ?? article.summary?.en?.gids_interpretation ?? article.summary?.en?.main_findings,
        zh: article.why_it_matters_zh ?? article.summary?.zh?.gids_interpretation ?? article.summary?.zh?.main_findings,
        fr: article.why_it_matters_fr ?? article.summary?.fr?.gids_interpretation ?? article.summary?.fr?.main_findings,
      },
      aliases: clean([article.doi, article.pmid, article.journal, article.study_type,
        ...(article.diseases ?? []).flatMap((item: any) => [item.name_en, item.name_zh, item.name_fr]),
        ...(article.countries ?? []).flatMap((item: any) => [item.code, item.name_en, item.name_zh, item.name_fr])]),
      updated_at: article.updated_at ?? article.published_at,
    });
  }
  return entries;
}
