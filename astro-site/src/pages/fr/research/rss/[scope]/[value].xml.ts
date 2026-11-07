import { readResearchIndex } from '../../../../../lib/generated-site-data.ts';
import type { APIRoute } from 'astro';
import {
  renderResearchFeedXml,
  type ResearchFeedData,
  type ResearchFeedDefinition,
} from '../../../../../lib/research-feed';

import { researchFeedCatalogue } from '../../../../../lib/research-feed-catalogue';

const fallbackSite = 'https://globalinfectiousdisease.com';

export function getStaticPaths() {
  return researchFeedCatalogue().definitions.map(definition => ({
    params: { scope: definition.scope, value: definition.value },
    props: { definition },
  }));
}

export const GET: APIRoute = ({ props, site }) => {
  const definition = props.definition as ResearchFeedDefinition;
  return new Response(renderResearchFeedXml({
    data: readResearchIndex() as ResearchFeedData,
    definition,
    selectArticles: researchFeedCatalogue().select,
    site: site ?? fallbackSite,
    language: 'fr',
  }), {
    headers: {
      'Content-Type': 'application/rss+xml; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
