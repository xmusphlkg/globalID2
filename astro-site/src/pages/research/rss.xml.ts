import { readResearchIndex } from '../../lib/generated-site-data.ts';
import type { APIRoute } from 'astro';
import { renderResearchFeedXml, type ResearchFeedData } from '../../lib/research-feed';

const fallbackSite = 'https://globalinfectiousdisease.com';

export const GET: APIRoute = ({ site }) => {
  return new Response(renderResearchFeedXml({
    data: readResearchIndex() as ResearchFeedData,
    site: site ?? fallbackSite,
  }), {
    headers: {
      'Content-Type': 'application/rss+xml; charset=utf-8',
      'Cache-Control': 'public, max-age=300',
    },
  });
};
