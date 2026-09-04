import type { APIRoute } from 'astro';
import researchData from '../../data/research/index.json';

export const prerender = true;

const graph: any = (researchData as any).knowledge_graph ?? { nodes: [], edges: [] };

// Export only fields the public graph UI consumes. The source graph also
// carries indexing metadata that belongs in the catalogue, not in every graph
// page response.
const nodes: any[] = (graph.nodes ?? []).map((node: any) => ({
  id: node.id,
  type: node.type,
  label: node.label,
  ...(node.url ? { url: node.url } : {}),
}));

const edges: any[] = (graph.edges ?? []).map((edge: any) => ({
  source: edge.source,
  relation: edge.relation,
  target: edge.target,
  ...(edge.confidence == null ? {} : { confidence: edge.confidence }),
  ...(edge.provenance ? { provenance: edge.provenance } : {}),
}));

export const GET: APIRoute = () => new Response(JSON.stringify({ nodes, edges }), {
  headers: {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'public, max-age=0, must-revalidate',
  },
});
