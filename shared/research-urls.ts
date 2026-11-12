/** Public pages have stable URLs; only their data and assets are versioned. */
export const RESEARCH_MAIN_ORIGIN = 'https://globalinfectiousdisease.com';
export const RESEARCH_PAPERS_ORIGIN = 'https://papers.globalinfectiousdisease.com';

export function isResearchPagePath(path: string): boolean {
  const pathname = path.split(/[?#]/, 1)[0];
  return /^\/(?:(?:zh|fr)\/)?research(?:\/|$)/.test(pathname)
    && (pathname.endsWith('/') || !pathname.split('/').at(-1)?.includes('.') || /\.html?$/.test(pathname));
}

export function isResearchResourcePath(path: string): boolean {
  const pathname = path.split(/[?#]/, 1)[0];
  return /^\/(?:_astro\/|scripts\/|site-data\/research(?:\/|-graph\.json$)|(?:zh\/|fr\/)?research\/)/.test(pathname)
    && /\.(?:json|css|m?js|svg|png|webp|jpe?g|avif|gif|ico|woff2?|ttf|map)$/.test(pathname);
}

export function researchPageUrl(path: string): string {
  const origin = /^\/(?:(?:zh|fr)\/)?research\/(?:articles|diseases)\//.test(path)
    ? RESEARCH_PAPERS_ORIGIN : RESEARCH_MAIN_ORIGIN;
  return new URL(path, origin).toString();
}
