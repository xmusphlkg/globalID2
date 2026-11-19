import { MAIN_ORIGIN, mainSiteBase } from './papers.ts';

type Language = 'en' | 'zh' | 'fr';
export interface DownloadScope { country?: string | null; disease?: string | null }

/** All archive entry points delegate selection and downloading to one page. */
export function downloadCenterHref(language: Language, scope: DownloadScope = {}, absolute = false): string {
  const query = new URLSearchParams();
  for (const key of ['country', 'disease'] as const) {
    const value = String(scope[key] ?? '').trim().toUpperCase();
    if (/^[A-Z0-9-]+$/.test(value)) query.set(key, value);
  }
  const suffix = query.size ? `?${query}` : '';
  return `${absolute ? MAIN_ORIGIN : mainSiteBase()}${language === 'en' ? '' : `/${language}`}/downloads/${suffix}`;
}

export function downloadScopeFromSummary(dataUrl: string): DownloadScope {
  const match = /^\/site-data\/(countries|diseases)\/([a-z0-9-]+)-summary\.json$/i.exec(dataUrl);
  return match ? { [match[1].toLowerCase() === 'countries' ? 'country' : 'disease']: match[2] } : {};
}
