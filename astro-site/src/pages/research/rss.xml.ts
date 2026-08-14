import type { APIRoute } from 'astro';
import researchData from '../../data/research/index.json';

const fallbackSite = 'https://globalinfectiousdisease.com';

function escapeXml(value: unknown): string {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

export const GET: APIRoute = ({ site }) => {
  const origin = site ?? new URL(fallbackSite);
  const articles = ((researchData as any).articles ?? []).slice(0, 50);
  const items = articles.map((article: any) => {
    const url = new URL(`/research/articles/${article.slug}/`, origin).toString();
    const description = article.why_it_matters_en
      ?? `A Research Radar record related to ${(article.diseases ?? []).map((item: any) => item.name_en).join(', ') || 'infectious disease'}.`;
    return `  <item>
    <title>${escapeXml(article.title)}</title>
    <link>${escapeXml(url)}</link>
    <guid isPermaLink="true">${escapeXml(url)}</guid>
    ${article.published_at ? `<pubDate>${escapeXml(new Date(article.published_at).toUTCString())}</pubDate>` : ''}
    <description>${escapeXml(description)}</description>
  </item>`;
  }).join('\n');
  const feedUrl = new URL('/research/rss.xml', origin).toString();
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>GIDS Research Radar</title>
  <link>${escapeXml(new URL('/research/', origin).toString())}</link>
  <description>Published infectious-disease literature metadata and quality-gated GIDS evidence summaries.</description>
  <language>en</language>
  <atom:link href="${escapeXml(feedUrl)}" rel="self" type="application/rss+xml" />
${items}
</channel>
</rss>
`;
  return new Response(body, { headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' } });
};
