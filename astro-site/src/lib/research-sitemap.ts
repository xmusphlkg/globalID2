/** Sitemap pages stay below protocol limits even as the literature grows. */
export const RESEARCH_SITEMAP_PAGE_SIZE = 45000;

export function researchSitemapPageCount(researchCount: number, paperCount: number): number {
  return Math.max(1, Math.ceil((researchCount + paperCount) / RESEARCH_SITEMAP_PAGE_SIZE));
}

export function researchSitemapPage<T>(research: T[], papers: T[], page: number): T[] {
  const start = (page - 1) * RESEARCH_SITEMAP_PAGE_SIZE, end = start + RESEARCH_SITEMAP_PAGE_SIZE;
  return [...research.slice(start, end), ...papers.slice(Math.max(0, start - research.length), Math.max(0, end - research.length))];
}

export function renderResearchSitemapIndex(origin: string, count: number): string {
  const entries = Array.from({ length: count }, (_, index) => {
    const url = new URL(`/research/sitemaps/${index + 1}.xml`, origin).toString().replaceAll('&', '&amp;');
    return `  <sitemap><loc>${url}</loc></sitemap>`;
  }).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${entries}\n</sitemapindex>\n`;
}
