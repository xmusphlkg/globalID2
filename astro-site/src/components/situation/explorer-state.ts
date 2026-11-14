import type { MapPoint, SiteIndex } from '../../utils/situationTypes.ts';
import { ageDays, DEFAULT_FILTERS, deduplicatePoints, filterPoints, sortPoints, type Filters, type WindowKey } from './model.ts';


export type Selection = { kind: 'place'; id: string } | { kind: 'country'; code: string } | null;

export interface Option {
  id: string;
  label: string;
  count: number;
}

export function applyDeepLinks(
  points: MapPoint[],
  site: SiteIndex,
  asOf: string,
): { filters: Filters; selection: Selection } {
  const params = new URLSearchParams(window.location.search);
  const disease = diseaseIdFromQuery(params.get('disease') ?? '', site, points);
  const countryRaw = (params.get('country') ?? '').toUpperCase();
  const country = (Object.hasOwn(site.countries, countryRaw) || points.some(point => point.country_code.toUpperCase() === countryRaw))
    ? countryRaw
    : '';
  const windowParam = params.get('window');
  const nextWindow: WindowKey = windowParam === '7' || windowParam === '90' || windowParam === 'all' || windowParam === '30'
    ? windowParam
    : DEFAULT_FILTERS.window;
  const place = params.get('place');
  const outbreak = params.get('outbreak');
  const match = place
    ? points.find(point => point.alert_id === place) ?? null
    : outbreak
      ? sortPoints(points.filter(point => point.outbreak_id === outbreak), asOf)[0] ?? null
      : null;
  const filters: Filters = {
    disease: disease || '',
    country,
    source: points.some(point => point.source_id === params.get('source')) ? params.get('source')! : '',
    window: !windowParam && match && ageDays(match.published_date, asOf) > 30 ? 'all' : nextWindow,
  };
  const candidates = deduplicatePoints(sortPoints(filterPoints(points, filters, asOf), asOf));
  const selected = match && (candidates.find(point => point.alert_id === match.alert_id)
    ?? candidates.find(point => point.outbreak_id === match.outbreak_id
      && point.country_code === match.country_code && point.admin_area === match.admin_area
      && point.published_date === match.published_date));
  return {
    filters,
    selection: selected
      ? { kind: 'place', id: selected.alert_id }
      : country
        ? { kind: 'country', code: country }
        : null,
  };
}

export function tally(points: MapPoint[], idOf: (point: MapPoint) => string | null, labelOf: (point: MapPoint) => string): Option[] {
  const counts = new Map<string, Option>();
  for (const point of points) {
    const id = idOf(point);
    if (!id) continue;
    const entry = counts.get(id) ?? { id, label: labelOf(point), count: 0 };
    entry.count += 1;
    counts.set(id, entry);
  }
  return [...counts.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function diseaseIdFromQuery(value: string, site: SiteIndex, points: MapPoint[]): string {
  const upper = value.trim().toUpperCase();
  if (!upper) return '';
  if (site.diseases[upper] || points.some(point => (point.disease_id ?? '').toUpperCase() === upper)) return upper;
  const bySlug = Object.entries(site.diseases).find(([, disease]) => disease.slug === value.trim().toLowerCase());
  return bySlug?.[0] ?? '';
}
