import { renderSitemapXml, type SitemapEntry } from './sitemap-entries.ts';

/** Leave room for multilingual alternate URLs below the Pages asset limit. */
export const RESEARCH_SITEMAP_PAGE_SIZE = 10000;
export const RESEARCH_SITEMAP_MAX_BYTES = 20 * 1024 * 1024;

/** Count UTF-8 XML bytes as well as URLs; long hreflang URLs can dominate size. */
export function researchSitemapPages(research: SitemapEntry[], papers: SitemapEntry[], origin: string,
  maxBytes = RESEARCH_SITEMAP_MAX_BYTES): SitemapEntry[][] {
  const pages: SitemapEntry[][] = [[]];
  let bytes = 0;
  for (const entry of [...research, ...papers]) {
    // Including the XML envelope per entry is conservative: the rendered page
    // only writes it once, so every partition stays below this byte budget.
    const size = Buffer.byteLength(renderSitemapXml([entry], origin));
    if (size > maxBytes) throw new Error(`Research sitemap entry exceeds its byte budget: ${entry.path}`);
    let page = pages[pages.length - 1];
    if (page.length && (page.length >= RESEARCH_SITEMAP_PAGE_SIZE || bytes + size > maxBytes)) {
      page = [];
      pages.push(page);
      bytes = 0;
    }
    page.push(entry);
    bytes += size;
  }
  return pages;
}

export function renderResearchSitemapIndex(origin: string, count: number): string {
  const entries = Array.from({ length: count }, (_, index) => {
    const url = new URL(`/research/sitemaps/${index + 1}.xml`, origin).toString().replaceAll('&', '&amp;');
    return `  <sitemap><loc>${url}</loc></sitemap>`;
  }).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${entries}\n</sitemapindex>\n`;
}
