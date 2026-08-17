import type { APIRoute } from 'astro';
import researchData from '../../../../data/research/index.json';
import {
  buildResearchFeedDefinitions,
  renderResearchFeedXml,
  type ResearchFeedData,
  type ResearchFeedDefinition,
} from '../../../../lib/research-feed';

const fallbackSite = 'https://globalinfectiousdisease.com';
const data = researchData as ResearchFeedData;

export function getStaticPaths() {
  return buildResearchFeedDefinitions(data).map(definition => ({
    params: { scope: definition.scope, value: definition.value },
    props: { definition },
  }));
}

export const GET: APIRoute = ({ props, site }) => {
  const definition = props.definition as ResearchFeedDefinition;
  return new Response(renderResearchFeedXml({
    data,
    definition,
    site: site ?? fallbackSite,
  }), {
    headers: {
      'Content-Type': 'application/rss+xml; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
