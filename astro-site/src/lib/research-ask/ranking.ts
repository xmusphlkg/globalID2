import { articleHref } from '../papers.ts';
import { type ResearchAskArticle, type ResearchAskEvidence, type ResearchAskMatchReason, type ResearchAskCitation, type ResearchFacetKind, type SearchUnit } from './types.ts';
import { normalizeText, confident, containsTerm, searchUnits } from './query.ts';

export const FIELD_WEIGHTS = {
  diseases: 18,
  title: 16,
  countries: 14,
  topics: 11,
  study: 9,
  tags: 7,
  summaryEn: 5,
  summaryZh: 5,
  summaryFr: 5,
} as const;

export function articleFieldText(article: ResearchAskArticle) {
  const title = normalizeText(article.title);
  const diseases = normalizeText((article.diseases ?? []).filter(confident).flatMap((item) => [item.disease_id, item.name_en, item.name_zh, item.name_fr]).join(' '));
  const countries = normalizeText((article.countries ?? []).filter(confident).flatMap((item) => [item.code, item.name_en, item.name_zh, item.name_fr]).join(' '));
  const topics = normalizeText((article.topics ?? []).filter(confident).map((item) => item.name).join(' '));
  const study = normalizeText(article.study_type);
  const tags = normalizeText([
    ...(article.tags ?? []).flatMap((item) => typeof item === 'string' ? [item] : [item.name, item.label]),
    article.peer_review_status,
    article.open_access_status,
  ].join(' '));
  const summaryEn = normalizeText([
    article.why_it_matters_en,
    ...Object.values(article.summary?.en ?? {}),
  ].join(' '));
  const summaryZh = normalizeText([
    article.why_it_matters_zh,
    ...Object.values(article.summary?.zh ?? {}),
  ].join(' '));
  const summaryFr = normalizeText([
    article.why_it_matters_fr,
    ...Object.values(article.summary?.fr ?? {}),
  ].join(' '));
  return { title, diseases, countries, topics, study, tags, summaryEn, summaryZh, summaryFr };
}

export function recencyScore(value: string | null | undefined, now: Date): number {
  if (!value) return 0;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || timestamp > now.getTime()) return -20;
  const days = Math.max(0, (now.getTime() - timestamp) / 86_400_000);
  if (days <= 30) return 4;
  if (days <= 90) return 3;
  if (days <= 365) return 2;
  if (days <= 730) return 1;
  return 0;
}

export function summaryField(article: ResearchAskArticle, language: string, field: string): string | null {
  const value = article.summary?.[language]?.[field];
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

export function evidenceFinding(article: ResearchAskArticle, language: 'en' | 'zh' | 'fr'): string | null {
  return summaryField(article, language, 'main_findings')
    ?? summaryField(article, language, 'public_health_relevance')
    ?? (language === 'en' ? article.why_it_matters_en?.trim() : language === 'zh' ? article.why_it_matters_zh?.trim() : article.why_it_matters_fr?.trim())
    ?? null;
}

export function normalizedDoi(value: string | null | undefined): string | null {
  const doi = String(value ?? '').trim().toLowerCase()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//, '')
    .replace(/^doi:\s*/, '');
  return doi || null;
}

export function articleDeduplicationKey(article: ResearchAskArticle): string {
  const doi = normalizedDoi(article.doi);
  if (doi) return `doi:${doi}`;
  if (article.pmid) return `pmid:${String(article.pmid).trim()}`;
  if (article.pmcid) return `pmcid:${String(article.pmcid).trim().toUpperCase()}`;
  if (article.article_id) return `article:${article.article_id}`;
  if (article.slug) return `slug:${article.slug}`;
  const year = String(article.published_at ?? '').slice(0, 4);
  return `fallback:${normalizeText(article.title)}:${year}`;
}

export function articleTieBreaker(article: ResearchAskArticle): string {
  return [
    articleDeduplicationKey(article),
    String(article.article_id ?? ''),
    String(article.slug ?? ''),
    normalizeText(article.title),
  ].join('|');
}

export function citationFor(article: ResearchAskArticle, index: number): ResearchAskCitation {
  const doi = normalizedDoi(article.doi);
  const pmid = String(article.pmid ?? '').trim() || null;
  const sourceUrl = doi
    ? `https://doi.org/${doi}`
    : pmid
      ? `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`
      : article.pmcid
        ? `https://pmc.ncbi.nlm.nih.gov/articles/${String(article.pmcid).trim()}/`
        : article.open_access_url?.trim()
          || (article.slug ? articleHref(article.slug) : '/research/');
  const title = String(article.title ?? 'Untitled research record').trim();
  const journal = String(article.journal ?? '').trim();
  const year = String(article.published_at ?? '').slice(0, 4);
  return {
    index,
    marker: `[${index}]`,
    label: [title, journal, year].filter(Boolean).join(' — '),
    sourceUrl,
    doi,
    pmid,
  };
}

// This narrow, source-reviewed exception does not change provider metadata.
// Publisher full text and Europe PMC (PMID37700242) were checked 2026-11-11:
// the paper studies bacterial-isolate genomics, while its provider MeSH still
// includes Influenza, Human. Preserve that heading without implying confirmed
// influenza-virus evidence in an Ask answer.
export function sourceAboutnessReview(article: ResearchAskArticle, units: SearchUnit[]): ResearchAskEvidence['sourceReview'] {
  if (normalizedDoi(article.doi) !== '10.1186/s12879-023-08560-x') return undefined;
  if (!units.some(unit => unit.kind === 'disease' && (unit.aliases.includes('d038') || normalizeText(unit.label) === 'influenza'))) return undefined;
  return {
    noticeEn: 'The source includes an influenza subject heading, but this paper studies bacterial isolates. Independent influenza-virus evidence has not been established; this record is retained as background. Check the paper.',
    noticeZh: '来源含有流感标引，但原文研究对象为细菌分离株，尚未核实独立的流感病毒研究依据；本条保留为背景。核对原文。',
    noticeFr: 'La source comporte une indexation grippale, mais cette étude porte sur des isolats bactériens. Aucune preuve indépendante sur le virus grippal n’a été établie ; cette référence est conservée comme contexte. Consulter l’article.',
    sourceUrl: 'https://link.springer.com/article/10.1186/s12879-023-08560-x',
  };
}

export function expectedFacetField(kind: ResearchFacetKind): keyof ReturnType<typeof articleFieldText> | null {
  if (kind === 'disease') return 'diseases';
  if (kind === 'country') return 'countries';
  if (kind === 'topic') return 'topics';
  if (kind === 'study_type') return 'study';
  return null;
}

export function rankResearchArticles(
  query: string,
  articles: ResearchAskArticle[],
  options: { limit?: number; now?: Date } = {},
): ResearchAskEvidence[] {
  const units = searchUnits(query, articles);
  if (!units.length) return [];
  const now = options.now ?? new Date();
  const ranked = articles
    .map((article) => {
      const fields = articleFieldText(article);
      const matchReasons: ResearchAskMatchReason[] = [];
      let score = 0;
      for (const unit of units) {
        const matchedFields: string[] = [];
        const matchedAliases = new Set<string>();
        let points = 0;
        for (const [field, weight] of Object.entries(FIELD_WEIGHTS)) {
          const aliases = unit.aliases.filter((candidate) => field === 'diseases' && unit.kind === 'disease'
            ? (article.diseases ?? []).filter(confident).some(disease => [disease.disease_id, disease.name_en, disease.name_zh, disease.name_fr].some(value => normalizeText(value) === candidate))
            : containsTerm(fields[field as keyof typeof fields], candidate));
          if (!aliases.length) continue;
          aliases.forEach((alias) => matchedAliases.add(alias));
          matchedFields.push(field);
          points += weight;
        }
        if (!matchedFields.length) continue;
        const canonicalAlias = normalizeText(unit.label);
        const matchedAlias = matchedAliases.has(canonicalAlias)
          ? canonicalAlias
          : [...matchedAliases].sort((left, right) => right.length - left.length || left.localeCompare(right))[0];
        score += points;
        matchReasons.push({
          kind: unit.kind,
          queryTerm: unit.label,
          matchedAlias,
          fields: matchedFields,
          points,
        });
      }
      if (!score) return null;
      const domainUnits = units.filter((unit) => unit.kind !== 'keyword');
      const allUnitsMatched = matchReasons.length === units.length;
      const allFacetsExact = domainUnits.length > 0 && domainUnits.every((unit) => {
        const expected = expectedFacetField(unit.kind);
        return expected !== null && matchReasons.some((reason) => (
          reason.kind === unit.kind && reason.queryTerm === unit.label && reason.fields.includes(expected)
        ));
      });
      const sourceReview = sourceAboutnessReview(article, units);
      const evidenceLevel = allUnitsMatched && allFacetsExact && !sourceReview ? 'exact' : 'background';
      score += (matchReasons.length / units.length) * 5;
      if (evidenceLevel === 'exact') score += 8;
      const normalizedQuery = normalizeText(query);
      if (normalizedQuery.length >= 4 && fields.title.includes(normalizedQuery)) score += 5;
      score += recencyScore(article.published_at, now);
      score += Math.max(0, Math.min(1, Number(article.discovery_score ?? 0))) * 3;
      if (article.peer_review_status === 'peer_reviewed') score += 1;
      return {
        article,
        score: Math.round(score * 100) / 100,
        matchedTerms: matchReasons.map((reason) => reason.queryTerm),
        evidenceLevel,
        matchReasons,
        citation: citationFor(article, 0),
        findingEn: evidenceFinding(article, 'en'),
        findingZh: evidenceFinding(article, 'zh'),
        findingFr: evidenceFinding(article, 'fr'),
        ...(sourceReview ? { sourceReview } : {}),
      } satisfies ResearchAskEvidence;
    })
    .filter((item): item is ResearchAskEvidence => item !== null)
    .sort((left, right) => (
      Number(right.evidenceLevel === 'exact') - Number(left.evidenceLevel === 'exact')
      || right.score - left.score
      || String(right.article.published_at ?? '').localeCompare(String(left.article.published_at ?? ''))
      || articleTieBreaker(left.article).localeCompare(articleTieBreaker(right.article))
    ));
  const deduplicated: ResearchAskEvidence[] = [];
  const seen = new Set<string>();
  for (const item of ranked) {
    const key = articleDeduplicationKey(item.article);
    if (seen.has(key)) continue;
    seen.add(key);
    deduplicated.push(item);
  }
  return deduplicated
    .slice(0, Math.max(1, Math.min(options.limit ?? 12, 30)))
    .map((item, index) => ({ ...item, citation: citationFor(item.article, index + 1) }));
}
