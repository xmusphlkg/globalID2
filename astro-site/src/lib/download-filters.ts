import type { Language } from './surveillance-presentation.ts';

export type DownloadScope = 'country_code' | 'disease_id';
export type DownloadPart = {
  id: string;
  label?: string;
  country_code?: string;
  disease_id?: string;
  [key: string]: any;
};

type DownloadWindowOptions = { language?: Language; currentYear?: number };

/** Keep archive IDs stable; only the window containing this year ends in 'now'. */
export function downloadWindowLabel(part: DownloadPart, { language = 'en', currentYear = new Date().getFullYear() }: DownloadWindowOptions = {}): string {
  const years = /^(\d{4})-(\d{4})$/.exec(part.id);
  if (years) {
    const [, start, end] = years;
    const current = Number(start) <= currentYear && currentYear <= Number(end);
    const now = language === 'zh' ? '至今' : language === 'fr' ? 'aujourd’hui' : 'now';
    return `${start}–${current ? now : end}`;
  }
  return part.label?.split(' · ').at(-1) || part.id;
}

/** Scope and time are independent: the same window can contain many cells. */
export function downloadSelection(parts: DownloadPart[], field: DownloadScope, scope = 'all', period = 'all', options: DownloadWindowOptions = {}) {
  const scopes = [...new Set(parts.map(part => part[field]).filter((value): value is string => Boolean(value)))];
  const scoped = parts.filter(part => scope === 'all' || part[field] === scope);
  const windows = [...new Map(scoped.map(part => [part.id, {
    id: part.id, label: downloadWindowLabel(part, options),
  }])).values()];
  return { scopes, windows, parts: scoped.filter(part => period === 'all' || part.id === period) };
}

export type DownloadFilters = { country: string; disease: string; window: string; format: string };
export const emptyDownloadFilters: DownloadFilters = { country: '', disease: '', window: '', format: '' };
export function readDownloadFilters(search: string): DownloadFilters {
  const params = new URLSearchParams(search);
  const code = (key: string) => /^[a-z0-9-]+$/i.test(params.get(key) || '') ? params.get(key)!.toUpperCase() : '';
  const window = params.get('window') || '';
  const format = params.get('format') || '';
  return { country: code('country'), disease: code('disease'),
    window: /^[a-z0-9-]+$/i.test(window) ? window : '', format: ['csv', 'json', 'xlsx'].includes(format) ? format : '' };
}

export function downloadCenterSelection(parts: DownloadPart[], filters: DownloadFilters, options: DownloadWindowOptions = {}) {
  const scoped = parts.filter(part => (!filters.country || part.country_code === filters.country)
    && (!filters.disease || part.disease_id === filters.disease));
  return downloadSelection(scoped, 'country_code', 'all', filters.window || 'all', options);
}
