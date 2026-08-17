export interface ResearchAskArticle {
  article_id?: string;
  slug?: string;
  title?: string;
  doi?: string | null;
  pmid?: string | null;
  pmcid?: string | null;
  journal?: string | null;
  study_type?: string | null;
  published_at?: string | null;
  discovery_score?: number | null;
  peer_review_status?: string | null;
  open_access_status?: string | null;
  open_access_url?: string | null;
  tags?: Array<string | { name?: string; label?: string }>;
  diseases?: Array<{ disease_id?: string; name_en?: string; name_zh?: string; confidence?: number }>;
  countries?: Array<{ code?: string; name_en?: string; name_zh?: string; confidence?: number }>;
  topics?: Array<{ name?: string; confidence?: number }>;
  summary?: Record<string, Record<string, unknown>>;
  why_it_matters_en?: string | null;
  why_it_matters_zh?: string | null;
  related_signals?: ResearchAskSignal[];
}

export interface ResearchAskSignal {
  signal_id?: string;
  title?: string | null;
  disease_name_en?: string | null;
  disease_name_zh?: string | null;
  geographies?: Array<{ code?: string; name?: string; name_en?: string }>;
  relation_level?: string;
  data_through?: string | null;
  situation_url?: string | null;
  visibility?: string | null;
}

export interface ResearchAskEvidence {
  article: ResearchAskArticle;
  score: number;
  matchedTerms: string[];
  evidenceLevel: 'exact' | 'background';
  matchReasons: ResearchAskMatchReason[];
  citation: ResearchAskCitation;
  findingEn: string | null;
  findingZh: string | null;
}

export interface ResearchAskMatchReason {
  kind: 'disease' | 'country' | 'topic' | 'study_type' | 'keyword';
  queryTerm: string;
  matchedAlias: string;
  fields: string[];
  points: number;
}

export interface ResearchAskCitation {
  index: number;
  marker: string;
  label: string;
  sourceUrl: string;
  doi: string | null;
  pmid: string | null;
}

export interface ResearchAskGap {
  kind: 'no_relevant_records' | 'no_exact_evidence' | 'missing_structured_summary' | 'no_linked_surveillance';
  messageEn: string;
  messageZh: string;
  count?: number;
}

export interface ResearchAskAnswer {
  query: string;
  normalizedQuery: string;
  evidence: ResearchAskEvidence[];
  exactEvidence: ResearchAskEvidence[];
  backgroundEvidence: ResearchAskEvidence[];
  citations: ResearchAskCitation[];
  gaps: ResearchAskGap[];
  signals: ResearchAskSignal[];
  themes: Array<{ name: string; count: number }>;
  summaryEn: string;
  summaryZh: string;
  limitationsEn: string;
  limitationsZh: string;
}

const STOP_WORDS = new Set([
  'about', 'after', 'among', 'and', 'are', 'been', 'being', 'can', 'could', 'does', 'for',
  'evidence', 'from', 'global', 'have', 'how', 'in', 'into', 'latest', 'most', 'new', 'of', 'on', 'recent', 'research',
  'study', 'that', 'the', 'their', 'these', 'this', 'what', 'when', 'where', 'which', 'why',
  'with', 'would',
  '什么', '为何', '为什么', '如何', '哪些', '最新', '近期', '研究', '证据', '全球', '相关',
]);

type ResearchFacetKind = 'disease' | 'country' | 'topic' | 'study_type' | 'keyword';

interface AliasGroup {
  kind: Exclude<ResearchFacetKind, 'keyword'>;
  label: string;
  aliases: string[];
}

interface SearchUnit {
  kind: ResearchFacetKind;
  label: string;
  aliases: string[];
}

const STATIC_ALIAS_GROUPS: Array<{ kind: AliasGroup['kind']; label: string; aliases: string[] }> = [
  { kind: 'disease', label: 'Pertussis', aliases: ['pertussis', 'whooping cough', '百日咳'] },
  { kind: 'disease', label: 'Dengue', aliases: ['dengue', 'dengue fever', '登革热'] },
  { kind: 'disease', label: 'Influenza', aliases: ['influenza', 'flu', '流感'] },
  { kind: 'disease', label: 'Measles', aliases: ['measles', '麻疹'] },
  { kind: 'disease', label: 'Mpox', aliases: ['mpox', 'monkeypox', '猴痘'] },
  { kind: 'disease', label: 'COVID-19', aliases: ['covid 19', 'covid', 'sars cov 2', '新冠', '新型冠状病毒'] },
  { kind: 'country', label: 'Japan', aliases: ['japan', 'jp', '日本'] },
  { kind: 'country', label: 'Brazil', aliases: ['brazil', 'br', '巴西'] },
  { kind: 'country', label: 'China', aliases: ['china', 'cn', '中国'] },
  { kind: 'country', label: 'United States', aliases: ['united states', 'usa', 'u s', 'us', '美国'] },
  { kind: 'country', label: 'United Kingdom', aliases: ['united kingdom', 'uk', 'great britain', '英国'] },
  { kind: 'topic', label: 'Vaccination', aliases: ['vaccination', 'vaccine', 'vaccines', 'immunization', 'immunisation', '疫苗', '接种'] },
  { kind: 'topic', label: 'Surveillance', aliases: ['surveillance', 'monitoring', '监测'] },
  { kind: 'topic', label: 'Diagnostics', aliases: ['diagnostics', 'diagnostic', 'diagnosis', 'testing', '诊断', '检测'] },
  { kind: 'topic', label: 'Antimicrobial resistance', aliases: ['antimicrobial resistance', 'antibiotic resistance', 'amr', '耐药', '抗微生物药物耐药性'] },
  { kind: 'topic', label: 'Outbreak investigation', aliases: ['outbreak', 'outbreak investigation', '暴发', '疫情调查'] },
  { kind: 'topic', label: 'Modelling', aliases: ['modelling', 'modeling', 'transmission model', '模型', '建模'] },
  { kind: 'study_type', label: 'Cohort study', aliases: ['cohort', 'cohort study', '队列研究', '队列'] },
  { kind: 'study_type', label: 'Cross-sectional study', aliases: ['cross sectional', 'cross sectional study', '横断面研究', '横断面'] },
  { kind: 'study_type', label: 'Systematic review', aliases: ['systematic review', 'meta analysis', 'meta-analysis', '系统综述', '荟萃分析'] },
  { kind: 'study_type', label: 'Randomized trial', aliases: ['randomized trial', 'randomised trial', 'rct', '随机对照试验'] },
  { kind: 'study_type', label: 'Guideline', aliases: ['guideline', 'consensus statement', 'guidance', '指南', '共识'] },
];

const FIELD_WEIGHTS = {
  diseases: 18,
  title: 16,
  countries: 14,
  topics: 11,
  study: 9,
  tags: 7,
  summaryEn: 5,
  summaryZh: 5,
} as const;

function normalizeText(value: unknown): string {
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

function confident(value: { confidence?: number }): boolean {
  return Number(value.confidence ?? 1) >= 0.66;
}

function articleFieldText(article: ResearchAskArticle) {
  const title = normalizeText(article.title);
  const diseases = normalizeText((article.diseases ?? []).filter(confident).flatMap((item) => [item.disease_id, item.name_en, item.name_zh]).join(' '));
  const countries = normalizeText((article.countries ?? []).filter(confident).flatMap((item) => [item.code, item.name_en, item.name_zh]).join(' '));
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
  return { title, diseases, countries, topics, study, tags, summaryEn, summaryZh };
}

function containsTerm(haystack: string, term: string): boolean {
  if (!term) return false;
  if (/^[a-z0-9]+$/.test(term)) return (` ${haystack} `).includes(` ${term} `) || (term.length >= 5 && haystack.includes(term));
  return haystack.includes(term);
}

function normalizedAliases(values: unknown[]): string[] {
  return [...new Set(values.map(normalizeText).filter(Boolean))];
}

function mergeAliasGroups(groups: AliasGroup[]): AliasGroup[] {
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

function catalogueAliasGroups(articles: ResearchAskArticle[]): AliasGroup[] {
  const groups: AliasGroup[] = STATIC_ALIAS_GROUPS.map((group) => ({
    kind: group.kind,
    label: group.label,
    aliases: normalizedAliases(group.aliases),
  }));
  for (const article of articles) {
    for (const disease of (article.diseases ?? []).filter(confident)) {
      const aliases = normalizedAliases([disease.disease_id, disease.name_en, disease.name_zh]);
      if (aliases.length) groups.push({ kind: 'disease', label: disease.name_en || disease.name_zh || disease.disease_id || aliases[0], aliases });
    }
    for (const country of (article.countries ?? []).filter(confident)) {
      const aliases = normalizedAliases([country.code, country.name_en, country.name_zh]);
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

function searchUnits(query: string, articles: ResearchAskArticle[]): SearchUnit[] {
  const normalizedQuery = normalizeText(query);
  const matchedGroups = catalogueAliasGroups(articles).filter((group) => (
    group.aliases.some((alias) => containsTerm(normalizedQuery, alias))
  ));
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

function recencyScore(value: string | null | undefined, now: Date): number {
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

function summaryField(article: ResearchAskArticle, language: string, field: string): string | null {
  const value = article.summary?.[language]?.[field];
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function evidenceFinding(article: ResearchAskArticle, language: 'en' | 'zh'): string | null {
  return summaryField(article, language, 'main_findings')
    ?? summaryField(article, language, 'public_health_relevance')
    ?? (language === 'en' ? article.why_it_matters_en?.trim() : article.why_it_matters_zh?.trim())
    ?? null;
}

function normalizedDoi(value: string | null | undefined): string | null {
  const doi = String(value ?? '').trim().toLowerCase()
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//, '')
    .replace(/^doi:\s*/, '');
  return doi || null;
}

function articleDeduplicationKey(article: ResearchAskArticle): string {
  const doi = normalizedDoi(article.doi);
  if (doi) return `doi:${doi}`;
  if (article.pmid) return `pmid:${String(article.pmid).trim()}`;
  if (article.pmcid) return `pmcid:${String(article.pmcid).trim().toUpperCase()}`;
  if (article.article_id) return `article:${article.article_id}`;
  if (article.slug) return `slug:${article.slug}`;
  const year = String(article.published_at ?? '').slice(0, 4);
  return `fallback:${normalizeText(article.title)}:${year}`;
}

function articleTieBreaker(article: ResearchAskArticle): string {
  return [
    articleDeduplicationKey(article),
    String(article.article_id ?? ''),
    String(article.slug ?? ''),
    normalizeText(article.title),
  ].join('|');
}

function citationFor(article: ResearchAskArticle, index: number): ResearchAskCitation {
  const doi = normalizedDoi(article.doi);
  const pmid = String(article.pmid ?? '').trim() || null;
  const sourceUrl = doi
    ? `https://doi.org/${doi}`
    : pmid
      ? `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`
      : article.pmcid
        ? `https://pmc.ncbi.nlm.nih.gov/articles/${String(article.pmcid).trim()}/`
        : article.open_access_url?.trim()
          || (article.slug ? `/research/articles/${article.slug}/` : '/research/');
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

function expectedFacetField(kind: ResearchFacetKind): keyof ReturnType<typeof articleFieldText> | null {
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
          const aliases = unit.aliases.filter((candidate) => containsTerm(fields[field as keyof typeof fields], candidate));
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
      const evidenceLevel = allUnitsMatched && allFacetsExact ? 'exact' : 'background';
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

function collectSignals(evidence: ResearchAskEvidence[]): ResearchAskSignal[] {
  const rows = new Map<string, ResearchAskSignal>();
  for (const item of evidence) {
    for (const signal of item.article.related_signals ?? []) {
      if (signal.visibility && signal.visibility !== 'public') continue;
      const key = String(signal.signal_id ?? `${signal.disease_name_en}:${signal.data_through}`);
      if (!rows.has(key)) rows.set(key, signal);
    }
  }
  return [...rows.values()].slice(0, 8);
}

function collectThemes(evidence: ResearchAskEvidence[]): Array<{ name: string; count: number }> {
  const counts = new Map<string, number>();
  for (const item of evidence) {
    for (const topic of item.article.topics ?? []) {
      const name = String(topic.name ?? '').trim();
      if (!name || Number(topic.confidence ?? 1) < 0.66) continue;
      counts.set(name, (counts.get(name) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name))
    .slice(0, 6);
}

function collectGaps(
  evidence: ResearchAskEvidence[],
  exactEvidence: ResearchAskEvidence[],
  signals: ResearchAskSignal[],
): ResearchAskGap[] {
  if (!evidence.length) {
    return [{
      kind: 'no_relevant_records',
      messageEn: 'No indexed record matched the requested disease, geography, topic, or study design closely enough.',
      messageZh: '当前索引中没有与所问疾病、地区、主题或研究设计足够匹配的记录。',
    }];
  }
  const gaps: ResearchAskGap[] = [];
  if (!exactEvidence.length) {
    gaps.push({
      kind: 'no_exact_evidence',
      messageEn: 'Only background matches were found; no record matched every requested domain facet.',
      messageZh: '目前仅检索到背景匹配；没有记录同时满足问题中的全部领域条件。',
    });
  }
  const missingSummary = evidence.filter((item) => !item.findingEn && !item.findingZh).length;
  if (missingSummary) {
    gaps.push({
      kind: 'missing_structured_summary',
      count: missingSummary,
      messageEn: `${missingSummary} cited record${missingSummary === 1 ? '' : 's'} lack a published structured finding; consult the source directly.`,
      messageZh: `${missingSummary} 条已引用记录尚无已发布的结构化研究结果，请直接阅读原始来源。`,
    });
  }
  if (!signals.length) {
    gaps.push({
      kind: 'no_linked_surveillance',
      messageEn: 'No public GIDS surveillance signal is currently linked to these records.',
      messageZh: '这些记录当前没有关联的公开 GIDS 监测信号。',
    });
  }
  return gaps;
}

function citationMarkers(evidence: ResearchAskEvidence[]): string {
  return evidence.map((item) => item.citation.marker).join(', ');
}

export function answerResearchQuestion(
  query: string,
  articles: ResearchAskArticle[],
  options: { limit?: number; now?: Date } = {},
): ResearchAskAnswer {
  const evidence = rankResearchArticles(query, articles, options);
  const exactEvidence = evidence.filter((item) => item.evidenceLevel === 'exact');
  const backgroundEvidence = evidence.filter((item) => item.evidenceLevel === 'background');
  const signals = collectSignals(evidence);
  const themes = collectThemes(evidence);
  const gaps = collectGaps(evidence, exactEvidence, signals);
  const themeEn = themes.length ? ` Leading indexed themes: ${themes.slice(0, 3).map((item) => item.name).join(', ')}.` : '';
  const themeZh = themes.length ? ` 主要索引主题为：${themes.slice(0, 3).map((item) => item.name).join('、')}。` : '';
  const exactMarkers = citationMarkers(exactEvidence);
  const backgroundMarkers = citationMarkers(backgroundEvidence);
  const summaryEn = evidence.length
    ? `Found ${exactEvidence.length} exact cited record${exactEvidence.length === 1 ? '' : 's'}${exactMarkers ? ` ${exactMarkers}` : ''} and ${backgroundEvidence.length} background record${backgroundEvidence.length === 1 ? '' : 's'}${backgroundMarkers ? ` ${backgroundMarkers}` : ''}. ${signals.length} linked public surveillance signal${signals.length === 1 ? '' : 's'} provide context only.${themeEn}`
    : 'No sufficiently relevant published Research Radar records were found. Try a disease, country, study design, or public-health topic.';
  const summaryZh = evidence.length
    ? `检索到 ${exactEvidence.length} 条精确引用记录${exactMarkers ? ` ${exactMarkers}` : ''}，以及 ${backgroundEvidence.length} 条背景记录${backgroundMarkers ? ` ${backgroundMarkers}` : ''}。另有 ${signals.length} 条公开监测信号，仅作为背景信息。${themeZh}`
    : '未找到相关度足够高的已发布研究记录。请尝试输入疾病、国家、研究设计或公共卫生主题。';
  return {
    query: query.trim(),
    normalizedQuery: normalizeText(query),
    evidence,
    exactEvidence,
    backgroundEvidence,
    citations: evidence.map((item) => item.citation),
    gaps,
    signals,
    themes,
    summaryEn,
    summaryZh,
    limitationsEn: 'This is deterministic, explainable catalogue retrieval—not a causal synthesis, clinical recommendation, or disease-risk assessment. Surveillance links provide context and do not show that a study explains or predicts a signal. Verify every finding in its cited source.',
    limitationsZh: '这是确定、可解释的目录检索，不是因果综合、临床建议或疾病风险评估。监测关联仅提供背景，不能证明研究解释或预测了某个信号。请逐条核对所引用的原始来源。',
  };
}
