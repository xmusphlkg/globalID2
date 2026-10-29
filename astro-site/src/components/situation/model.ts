/** Pure map logic: recency, filtering, ordering and placement. Kept free of React and Node so it is unit-testable. */

import type { MapPoint, SituationAlertRecord } from '../../utils/situationTypes.ts';

export type Recency = 'fresh' | 'recent' | 'older' | 'stale';
export type WindowKey = '7' | '30' | '90' | 'all';

export interface Filters {
  disease: string;
  country: string;
  source: string;
  window: WindowKey;
}

export const EMPTY_FILTERS: Filters = { disease: '', country: '', source: '', window: 'all' };

const DAY_MS = 86_400_000;

/** Colour encodes how recent the news is, never how severe a model thinks it is. */
export const RECENCY_COLORS: Record<Recency, string> = {
  fresh: '#e0483d',
  recent: '#f0a030',
  older: '#6aa7c8',
  stale: '#8d9aa8',
};

function dayNumber(date: string): number {
  const parsed = Date.parse(`${date.slice(0, 10)}T00:00:00Z`);
  return Number.isFinite(parsed) ? Math.floor(parsed / DAY_MS) : Number.NaN;
}

export function ageDays(date: string, asOf: string): number {
  return dayNumber(asOf) - dayNumber(date);
}

export function recencyOf(date: string, asOf: string): Recency {
  const age = ageDays(date, asOf);
  if (!(age <= 7)) return age <= 30 ? 'recent' : age <= 90 ? 'older' : 'stale';
  return 'fresh';
}

export function filterPoints<T extends SituationAlertRecord>(points: T[], filters: Filters, asOf: string): T[] {
  const limit = filters.window === 'all' ? Infinity : Number(filters.window);
  return points.filter(point => {
    if (filters.disease && (point.disease_id ?? point.disease_label) !== filters.disease) return false;
    if (filters.country && point.country_code !== filters.country) return false;
    if (filters.source && point.source_id !== filters.source) return false;
    return !(ageDays(point.published_date, asOf) > limit);
  });
}

/** Points updated in the last 7 days first, then newest first. */
export function sortPoints<T extends SituationAlertRecord>(points: T[], asOf: string): T[] {
  const rank = (point: T) => (recencyOf(point.published_date, asOf) === 'fresh' ? 0 : 1);
  return [...points].sort(
    (a, b) =>
      rank(a) - rank(b) ||
      b.published_date.localeCompare(a.published_date) ||
      a.alert_id.localeCompare(b.alert_id),
  );
}

type Ring = number[][];

function ringBox(ring: Ring): { minX: number; maxX: number; minY: number; maxY: number } {
  const box = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  for (const [x, y] of ring) {
    box.minX = Math.min(box.minX, x);
    box.maxX = Math.max(box.maxX, x);
    box.minY = Math.min(box.minY, y);
    box.maxY = Math.max(box.maxY, y);
  }
  return box;
}

/** Centre of the largest landmass of a feature, so island territories do not drag a marker into the sea. */
export function featureAnchor(feature: any): [number, number] | null {
  const geometry = feature?.geometry;
  const polygons: Ring[] =
    geometry?.type === 'Polygon'
      ? [geometry.coordinates?.[0]]
      : geometry?.type === 'MultiPolygon'
        ? (geometry.coordinates ?? []).map((polygon: Ring[]) => polygon?.[0])
        : [];
  let best: ReturnType<typeof ringBox> | null = null;
  let bestArea = -1;
  for (const ring of polygons) {
    if (!Array.isArray(ring) || ring.length === 0) continue;
    const box = ringBox(ring);
    const area = (box.maxX - box.minX) * (box.maxY - box.minY);
    if (area > bestArea) {
      best = box;
      bestArea = area;
    }
  }
  return best ? [(best.minX + best.maxX) / 2, (best.minY + best.maxY) / 2] : null;
}

export function buildAnchors(worldGeoJson: any, nameToIso: Record<string, string>): Record<string, [number, number]> {
  const anchors: Record<string, [number, number]> = {};
  for (const feature of worldGeoJson?.features ?? []) {
    const iso = nameToIso[String(feature?.properties?.name ?? '')];
    const anchor = iso ? featureAnchor(feature) : null;
    if (iso && anchor && !anchors[iso]) anchors[iso] = anchor;
  }
  return anchors;
}

export interface PlacedPoint<T> {
  point: T;
  lng: number;
  lat: number;
}

/** Points that share a country fan out around its anchor so none hides another. */
export function placePoints<T extends SituationAlertRecord>(
  points: T[],
  anchors: Record<string, [number, number]>,
): { placed: PlacedPoint<T>[]; unplaced: T[] } {
  const seen = new Map<string, number>();
  const placed: PlacedPoint<T>[] = [];
  const unplaced: T[] = [];
  for (const point of points) {
    const anchor = anchors[point.country_code];
    if (!anchor) {
      unplaced.push(point);
      continue;
    }
    const index = seen.get(point.country_code) ?? 0;
    seen.set(point.country_code, index + 1);
    const angle = index * 2.399963;
    const radius = index === 0 ? 0 : 1.1 * Math.sqrt(index);
    placed.push({ point, lng: anchor[0] + radius * Math.cos(angle), lat: anchor[1] + radius * Math.sin(angle) });
  }
  return { placed, unplaced };
}

export function placeLabel(point: Pick<MapPoint, 'admin_area' | 'country_code'>, countryName: string): string {
  return point.admin_area ? `${point.admin_area}, ${countryName}` : countryName;
}
