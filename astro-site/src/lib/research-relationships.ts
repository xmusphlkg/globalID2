import { localizedRegionName, RESEARCH_FACET_LABELS, RESEARCH_TOPIC_LABELS, STUDY_TYPE_LABELS, type Lang } from '../utils/i18n.ts';

type GraphNode = { id: string; type?: string; label?: string; name_zh?: string; name_fr?: string; code?: string; url?: string; slug?: string };
type GraphEdge = { id?: string; relation?: string; target?: string };

const relationNames: Record<string, Record<Lang, string>> = {
  ABOUT_DISEASE: { en: 'Disease', zh: '疾病', fr: 'Maladie' },
  ADDRESSES_TOPIC: { en: 'Topic', zh: '研究主题', fr: 'Thème' },
  STUDIED_IN: { en: 'Location', zh: '研究地区', fr: 'Lieu' },
  USES_STUDY_DESIGN: { en: 'Study design', zh: '研究设计', fr: 'Plan d’étude' },
  STUDIES_PATHOGEN: { en: 'Pathogen', zh: '病原体', fr: 'Agent pathogène' },
  HAS_PATHOGEN_TYPE: { en: 'Pathogen type', zh: '病原体类型', fr: 'Type d’agent pathogène' },
  STUDIED_POPULATION_SETTING: { en: 'Study setting', zh: '研究场景', fr: 'Cadre de l’étude' },
  STUDIES_POPULATION: { en: 'Population', zh: '研究人群', fr: 'Population' },
};

export function researchTermLabel(value: string | null | undefined, lang: Lang): string {
  if (!value) return '—';
  return (STUDY_TYPE_LABELS[value] ?? RESEARCH_TOPIC_LABELS[value] ?? RESEARCH_FACET_LABELS[value])?.[lang] ?? value;
}

/** Resolve relationship targets, rather than rendering predicate-only duplicate chips. */
export function researchRelationships(graph: { nodes?: GraphNode[]; edges?: GraphEdge[] }, lang: Lang) {
  const nodes = new Map((graph.nodes ?? []).map(node => [node.id, node]));
  return (graph.edges ?? []).flatMap((edge, index) => {
    const node = edge.target ? nodes.get(edge.target) : undefined;
    if (!node) return [];
    const names = { en: node.label, zh: node.name_zh, fr: node.name_fr };
    const target = node.type === 'country'
      ? localizedRegionName(lang, node.code, names)
      : names[lang] || researchTermLabel(node.label, lang);
    const relation = relationNames[edge.relation ?? '']?.[lang] ?? (lang === 'zh' ? '关联' : lang === 'fr' ? 'Relation' : 'Related');
    const href = node.type === 'country' && /^[a-z]{2}(?:-[a-z0-9]+)?$/i.test(node.code ?? '')
      ? `/research/countries/${node.code!.toLowerCase()}/`
      : node.url?.startsWith('/') && !node.url.startsWith('//') ? node.url
      : node.type === 'topic' && node.label ? `/research/topics/${node.label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')}/` : undefined;
    return [{ id: edge.id ?? `${edge.target}:${index}`, relation, target, href }];
  });
}
