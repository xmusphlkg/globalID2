import { readResearchIndex } from '../../lib/generated-site-data.ts';
import type { APIRoute } from 'astro';
import { localizedRegionName } from '../../utils/i18n';
import { localizedDiseaseName } from '../../utils/diseaseNames';
import { researchTermLabel } from '../../lib/research-relationships';

export const prerender = true;

export const GET: APIRoute = () => {
const graph: any = (readResearchIndex() as any).knowledge_graph ?? { nodes: [], edges: [] };

// Export only fields the public graph UI consumes. The source graph also
// carries indexing metadata that belongs in the catalogue, not in every graph
// page response.
const localizedNode = (node: any, lang: 'zh' | 'fr') => node.type === 'country' ? localizedRegionName(lang, node.code, { en: node.label, zh: node.name_zh, fr: node.name_fr }) : node.type === 'disease' ? localizedDiseaseName({ ...node, name_en: node.label }, lang) : (lang === 'zh' ? node.name_zh : node.name_fr) || researchTermLabel(node.label, lang);
const nodes: any[] = (graph.nodes ?? []).map((node: any) => ({
  id: node.id,
  type: node.type,
  label: node.label,
  ...(localizedNode(node, 'zh') !== node.label ? { label_zh: localizedNode(node, 'zh') } : {}),
  ...(localizedNode(node, 'fr') !== node.label ? { label_fr: localizedNode(node, 'fr') } : {}),
  ...(node.url ? { url: node.url } : {}),
}));

const edges: any[] = (graph.edges ?? []).map((edge: any) => ({
  source: edge.source,
  relation: edge.relation,
  target: edge.target,
  ...(edge.confidence == null ? {} : { confidence: edge.confidence }),
  ...(edge.provenance ? { provenance: edge.provenance } : {}),
}));

return new Response(JSON.stringify({ nodes, edges }), {
  headers: {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'public, max-age=0, must-revalidate',
  },
});

};
