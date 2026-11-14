import { type ResearchAskArticle, type ResearchAskSignal, type ResearchAskEvidence, type ResearchAskGap, type ResearchAskAnswer } from './types.ts';
import { normalizeText } from './query.ts';
import { rankResearchArticles } from './ranking.ts';

export function collectSignals(evidence: ResearchAskEvidence[]): ResearchAskSignal[] {
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

export function collectThemes(evidence: ResearchAskEvidence[]): Array<{ name: string; count: number }> {
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

export function collectGaps(
  evidence: ResearchAskEvidence[],
  exactEvidence: ResearchAskEvidence[],
  signals: ResearchAskSignal[],
): ResearchAskGap[] {
  if (!evidence.length) {
    return [{
      kind: 'no_relevant_records',
      messageEn: 'No indexed record matched the requested disease, geography, topic, or study design closely enough.',
      messageZh: '当前索引中没有与所问疾病、地区、主题或研究设计足够匹配的记录。',
      messageFr: 'Aucun enregistrement indexé ne correspond suffisamment à la maladie, à la zone géographique, au thème ou au type d’étude demandé.',
    }];
  }
  const gaps: ResearchAskGap[] = [];
  if (!exactEvidence.length) {
    gaps.push({
      kind: 'no_exact_evidence',
      messageEn: 'Only background matches were found; no record matched every requested domain facet.',
      messageZh: '目前仅检索到背景匹配；没有记录同时满足问题中的全部领域条件。',
      messageFr: 'Seuls des résultats contextuels ont été trouvés ; aucun enregistrement ne correspond à tous les critères demandés.',
    });
  }
  const missingSummary = evidence.filter((item) => !item.findingEn && !item.findingZh && !item.findingFr).length;
  if (missingSummary) {
    gaps.push({
      kind: 'missing_structured_summary',
      count: missingSummary,
      messageEn: `${missingSummary} cited record${missingSummary === 1 ? '' : 's'} lack a published structured finding; consult the source directly.`,
      messageZh: `${missingSummary} 条已引用记录尚无已发布的结构化研究结果，请直接阅读原始来源。`,
      messageFr: `${missingSummary} référence${missingSummary === 1 ? '' : 's'} citée${missingSummary === 1 ? '' : 's'} ne possède pas de résultat structuré publié ; consultez directement la source.`,
    });
  }
  if (!signals.length) {
    gaps.push({
      kind: 'no_linked_surveillance',
      messageEn: 'No public GIDS surveillance signal is currently linked to these records.',
      messageZh: '这些记录当前没有关联的公开 GIDS 监测信号。',
      messageFr: 'Aucun signal public de surveillance GIDS n’est actuellement lié à ces références.',
    });
  }
  return gaps;
}

export function citationMarkers(evidence: ResearchAskEvidence[]): string {
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
  const themeFr = themes.length ? ` Thèmes principaux indexés : ${themes.slice(0, 3).map((item) => item.name).join(', ')}.` : '';
  const exactMarkers = citationMarkers(exactEvidence);
  const backgroundMarkers = citationMarkers(backgroundEvidence);
  const summaryEn = evidence.length
    ? `Found ${exactEvidence.length} exact cited record${exactEvidence.length === 1 ? '' : 's'}${exactMarkers ? ` ${exactMarkers}` : ''} and ${backgroundEvidence.length} background record${backgroundEvidence.length === 1 ? '' : 's'}${backgroundMarkers ? ` ${backgroundMarkers}` : ''}. ${signals.length} linked public surveillance signal${signals.length === 1 ? '' : 's'} provide context only.${themeEn}`
    : 'No sufficiently relevant published Research Radar records were found. Try a disease, area, study design, or public-health topic.';
  const summaryZh = evidence.length
    ? `检索到 ${exactEvidence.length} 条精确引用记录${exactMarkers ? ` ${exactMarkers}` : ''}，以及 ${backgroundEvidence.length} 条背景记录${backgroundMarkers ? ` ${backgroundMarkers}` : ''}。另有 ${signals.length} 条公开监测信号，仅作为背景信息。${themeZh}`
    : '未找到相关度足够高的已发布研究记录。请尝试输入疾病、区域、研究设计或公共卫生主题。';
  const summaryFr = evidence.length
    ? `${exactEvidence.length} référence${exactEvidence.length === 1 ? '' : 's'} exacte${exactEvidence.length === 1 ? '' : 's'}${exactMarkers ? ` ${exactMarkers}` : ''} et ${backgroundEvidence.length} référence${backgroundEvidence.length === 1 ? '' : 's'} contextuelle${backgroundEvidence.length === 1 ? '' : 's'}${backgroundMarkers ? ` ${backgroundMarkers}` : ''} ont été trouvées. ${signals.length} signal${signals.length === 1 ? '' : 's'} public${signals.length === 1 ? '' : 's'} lié${signals.length === 1 ? '' : 's'} fournit${signals.length === 1 ? '' : 'ssent'} uniquement un contexte.${themeFr}`
    : 'Aucune publication Research Radar suffisamment pertinente n’a été trouvée. Essayez une maladie, une zone, un type d’étude ou un thème de santé publique.';
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
    summaryFr,
    limitationsEn: 'This is deterministic, explainable catalogue retrieval—not a causal synthesis, clinical recommendation, or disease-risk assessment. Surveillance links provide context and do not show that a study explains or predicts a signal. Verify every finding in its cited source.',
    limitationsZh: '这是确定、可解释的目录检索，不是因果综合、临床建议或疾病风险评估。监测关联仅提供背景，不能证明研究解释或预测了某个信号。请逐条核对所引用的原始来源。',
    limitationsFr: 'Il s’agit d’une recherche déterministe et explicable dans le catalogue, pas d’une synthèse causale, d’une recommandation clinique ni d’une évaluation du risque. Les liens de surveillance fournissent un contexte et ne montrent pas qu’une étude explique ou prédit un signal. Vérifiez chaque résultat dans sa source.',
  };
}
