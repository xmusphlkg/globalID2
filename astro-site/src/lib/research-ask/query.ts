import { type ResearchAskArticle, type AliasGroup, type SearchUnit } from './types.ts';

export const STOP_WORDS = new Set([
  'about', 'after', 'among', 'and', 'are', 'been', 'being', 'can', 'could', 'does', 'for',
  'evidence', 'from', 'global', 'have', 'how', 'in', 'into', 'latest', 'most', 'new', 'of', 'on', 'recent', 'research',
  'study', 'that', 'the', 'their', 'these', 'this', 'what', 'when', 'where', 'which', 'why',
  'with', 'would',
  'dans', 'des', 'les', 'une', 'pour', 'sur', 'avec', 'entre', 'quel', 'quelle', 'quels', 'quelles', 'est', 'sont', 'au', 'aux', 'du', 'de', 'la', 'le',
  '什么', '为何', '为什么', '如何', '哪些', '最新', '近期', '研究', '证据', '全球', '相关',
]);

export const STATIC_ALIAS_GROUPS: Array<{ kind: AliasGroup['kind']; label: string; aliases: string[] }> = [
  { kind: 'disease', label: 'Pertussis', aliases: ['pertussis', 'whooping cough', 'coqueluche', '百日咳'] },
  { kind: 'disease', label: 'Dengue', aliases: ['dengue', 'dengue fever', 'fièvre dengue', '登革热'] },
  { kind: 'disease', label: 'Influenza', aliases: ['influenza', 'flu', 'grippe', '流感'] },
  { kind: 'disease', label: 'Measles', aliases: ['measles', 'rougeole', '麻疹'] },
  { kind: 'disease', label: 'Mpox', aliases: ['mpox', 'monkeypox', 'variole du singe', '猴痘'] },
  { kind: 'disease', label: 'COVID-19', aliases: ['covid 19', 'covid', 'sars cov 2', 'covid 19', '新冠', '新型冠状病毒'] },
  { kind: 'country', label: 'Japan', aliases: ['japan', 'jp', 'japon', '日本'] },
  { kind: 'country', label: 'Brazil', aliases: ['brazil', 'brésil', 'br', '巴西'] },
  { kind: 'country', label: 'China', aliases: ['china', 'cn', 'chine', '中国'] },
  { kind: 'country', label: 'United States', aliases: ['united states', 'usa', 'u s', 'us', 'états unis', '美国'] },
  { kind: 'country', label: 'United Kingdom', aliases: ['united kingdom', 'uk', 'great britain', 'royaume uni', '英国'] },
  { kind: 'topic', label: 'Vaccination', aliases: ['vaccination', 'vaccine', 'vaccines', 'immunization', 'immunisation', 'vaccination', '疫苗', '接种'] },
  { kind: 'topic', label: 'Surveillance', aliases: ['surveillance', 'monitoring', 'surveillance épidémiologique', '监测'] },
  { kind: 'topic', label: 'Diagnostics', aliases: ['diagnostics', 'diagnostic', 'diagnosis', 'testing', 'diagnostic', '检测'] },
  { kind: 'topic', label: 'Antimicrobial resistance', aliases: ['antimicrobial resistance', 'antibiotic resistance', 'amr', '耐药', '抗微生物药物耐药性'] },
  { kind: 'topic', label: 'Outbreak investigation', aliases: ['outbreak', 'outbreak investigation', '暴发', '疫情调查'] },
  { kind: 'topic', label: 'Modelling', aliases: ['modelling', 'modeling', 'transmission model', '模型', '建模'] },
  { kind: 'study_type', label: 'Cohort study', aliases: ['cohort', 'cohort study', '队列研究', '队列'] },
  { kind: 'study_type', label: 'Cross-sectional study', aliases: ['cross sectional', 'cross sectional study', '横断面研究', '横断面'] },
  { kind: 'study_type', label: 'Systematic review', aliases: ['systematic review', 'meta analysis', 'meta-analysis', '系统综述', '荟萃分析'] },
  { kind: 'study_type', label: 'Randomized trial', aliases: ['randomized trial', 'randomised trial', 'rct', '随机对照试验'] },
  { kind: 'study_type', label: 'Guideline', aliases: ['guideline', 'consensus statement', 'guidance', '指南', '共识'] },
];

export function normalizeText(value: unknown): string {
  return String(value ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
}

export function researchQueryTerms(query: string): string[] {
  const normalized = normalizeText(query);
  const latin = normalized.split(/\s+/).filter((term) => term.length >= 2 && !STOP_WORDS.has(term));
  const compactCjk = [...normalized.matchAll(/[\p{Script=Han}]{2,}/gu)]
    .flatMap((match) => {
      const value = match[0];
      if (value.length <= 4) return [value];
      return [value, ...Array.from({ length: value.length - 1 }, (_, index) => value.slice(index, index + 2))];
    })
    .filter((term) => !STOP_WORDS.has(term));
  return [...new Set([...latin, ...compactCjk])].slice(0, 32);
}

export function confident(value: { confidence?: number }): boolean {
  return Number(value.confidence ?? 1) >= 0.66;
}

export function containsTerm(haystack: string, term: string): boolean {
  if (!term) return false;
  if (!/[\p{Script=Han}]/u.test(term)) return (` ${haystack} `).includes(` ${term} `);
  return haystack.includes(term);
}

export function normalizedAliases(values: unknown[]): string[] {
  // A two-letter country code can be an ordinary question word (for example,
  // India's `IN` is also the preposition "in"). Keep the full country name
  // searchable, but do not let a stop-word code become an implicit facet.
  return [...new Set(values.map(normalizeText).filter((alias) => (
    Boolean(alias) && !STOP_WORDS.has(alias)
  )))];
}

export function mergeAliasGroups(groups: AliasGroup[]): AliasGroup[] {
  const merged: AliasGroup[] = [];
  for (const group of groups) {
    const aliases = new Set(normalizedAliases(group.aliases));
    let label = group.label;
    for (let index = merged.length - 1; index >= 0; index -= 1) {
      const existing = merged[index];
      if (existing.kind !== group.kind || !existing.aliases.some((alias) => aliases.has(alias))) continue;
      existing.aliases.forEach((alias) => aliases.add(alias));
      label = existing.label || label;
      merged.splice(index, 1);
    }
    merged.push({ kind: group.kind, label, aliases: [...aliases].sort() });
  }
  return merged;
}

export function catalogueAliasGroups(articles: ResearchAskArticle[]): AliasGroup[] {
  const groups: AliasGroup[] = STATIC_ALIAS_GROUPS.map((group) => ({
    kind: group.kind,
    label: group.label,
    aliases: normalizedAliases(group.aliases),
  }));
  for (const article of articles) {
    for (const disease of (article.diseases ?? []).filter(confident)) {
      const aliases = normalizedAliases([disease.disease_id, disease.name_en, disease.name_zh, disease.name_fr]);
      if (aliases.length) groups.push({ kind: 'disease', label: disease.name_en || disease.name_zh || disease.disease_id || aliases[0], aliases });
    }
    for (const country of (article.countries ?? []).filter(confident)) {
      const aliases = normalizedAliases([country.code, country.name_en, country.name_zh, country.name_fr]);
      if (aliases.length) groups.push({ kind: 'country', label: country.name_en || country.name_zh || country.code || aliases[0], aliases });
    }
    for (const topic of (article.topics ?? []).filter(confident)) {
      const aliases = normalizedAliases([topic.name]);
      if (aliases.length) groups.push({ kind: 'topic', label: topic.name || aliases[0], aliases });
    }
    const studyAliases = normalizedAliases([article.study_type]);
    if (studyAliases.length) groups.push({ kind: 'study_type', label: article.study_type || studyAliases[0], aliases: studyAliases });
  }
  return mergeAliasGroups(groups);
}

export function searchUnits(query: string, articles: ResearchAskArticle[]): SearchUnit[] {
  const normalizedQuery = normalizeText(query);
  const candidates = catalogueAliasGroups(articles).filter((group) => (
    group.aliases.some((alias) => containsTerm(normalizedQuery, alias))
  ));
  // A shorter disease alias inside a specific disease name is not a second
  // query concept (流感嗜血杆菌 does not independently request 流感).
  const matchedGroups = candidates.filter(group => group.aliases.some(alias => {
    if (!containsTerm(normalizedQuery, alias)) return false;
    for (let at = normalizedQuery.indexOf(alias); at >= 0; at = normalizedQuery.indexOf(alias, at + 1)) {
      const latin = !/[\p{Script=Han}]/u.test(alias);
      if (latin && ((at > 0 && normalizedQuery[at - 1] !== ' ') || (at + alias.length < normalizedQuery.length && normalizedQuery[at + alias.length] !== ' '))) continue;
      const shadowed = candidates.some(other => other !== group && other.kind === group.kind && other.aliases.some(longer => {
        if (longer.length <= alias.length || !containsTerm(normalizedQuery, longer)) return false;
        for (let start = normalizedQuery.indexOf(longer); start >= 0; start = normalizedQuery.indexOf(longer, start + 1)) {
          if (start <= at && at + alias.length <= start + longer.length) return true;
        }
        return false;
      }));
      if (!shadowed) return true;
    }
    return false;
  }));
  let residualQuery = normalizedQuery;
  for (const alias of matchedGroups.flatMap((group) => group.aliases).sort((left, right) => right.length - left.length)) {
    if (!containsTerm(normalizedQuery, alias)) continue;
    residualQuery = residualQuery.replaceAll(alias, ' ');
  }
  const coveredByAlias = (term: string) => matchedGroups.some((group) => (
    group.aliases.some((alias) => containsTerm(alias, term) || containsTerm(term, alias))
  ));
  const rawUnits: SearchUnit[] = researchQueryTerms(residualQuery)
    .filter((term) => !coveredByAlias(term))
    .map((term) => ({ kind: 'keyword', label: term, aliases: [term] }));
  return [
    ...matchedGroups.map((group) => ({ kind: group.kind, label: group.label, aliases: group.aliases })),
    ...rawUnits,
  ].slice(0, 32);
}
