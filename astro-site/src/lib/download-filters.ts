export type DownloadScope = 'country_code' | 'disease_id';
export type DownloadPart = {
  id: string;
  label?: string;
  country_code?: string;
  disease_id?: string;
  [key: string]: any;
};

/** Stable window bounds must not turn an old archive into 'now'. */
export function downloadWindowLabel(part: DownloadPart): string {
  return /^\d{4}-\d{4}$/.test(part.id) ? part.id.replace('-', '–') : part.label?.split(' · ').at(-1) || part.id;
}

/** Scope and time are independent: the same window can contain many cells. */
export function downloadSelection(parts: DownloadPart[], field: DownloadScope, scope = 'all', period = 'all') {
  const scopes = [...new Set(parts.map(part => part[field]).filter((value): value is string => Boolean(value)))];
  const scoped = parts.filter(part => scope === 'all' || part[field] === scope);
  const windows = [...new Map(scoped.map(part => [part.id, {
    id: part.id, label: downloadWindowLabel(part),
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

export function downloadCenterSelection(parts: DownloadPart[], filters: DownloadFilters) {
  const scoped = parts.filter(part => (!filters.country || part.country_code === filters.country)
    && (!filters.disease || part.disease_id === filters.disease));
  return downloadSelection(scoped, 'country_code', 'all', filters.window || 'all');
}
