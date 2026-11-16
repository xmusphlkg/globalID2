/** Pure map logic: recency, filtering, ordering and placement. Kept free of React and Node so it is unit-testable. */

import type { HostType, MapPoint, SituationAlertRecord } from '../../utils/situationTypes.ts';
import { resolveReportingPeriod } from './report-period.ts';

type FilterablePoint = {
  alert_id: string;
  published_date: string;
  country_code: string;
  source_id: string;
  disease_id?: string | null;
  disease_label: string;
  host_type?: HostType | null;
  lat?: number;
  lng?: number;
};

export type Recency = 'fresh' | 'recent' | 'older' | 'stale';
export type WindowKey = '7' | '30' | '90' | 'all';

export interface Filters {
  disease: string;
  country: string;
  source: string;
  host?: HostType | '';
  window: WindowKey;
}

/** The map opens on the last 30 days; a year of points buries what is new. */
export const DEFAULT_FILTERS: Filters = { disease: '', country: '', source: '', host: '', window: '30' };

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

export function filterPoints<T extends FilterablePoint>(points: T[], filters: Filters, asOf: string): T[] {
  const limit = filters.window === 'all' ? Infinity : Number(filters.window);
  return points.filter(point => {
    if (filters.disease && (point.disease_id ?? point.disease_label) !== filters.disease) return false;
    if (filters.country && point.country_code !== filters.country) return false;
    if (filters.source && point.source_id !== filters.source) return false;
    if (filters.host && (point.host_type ?? 'unknown') !== filters.host) return false;
    const age = ageDays(point.published_date, asOf);
    return Number.isFinite(age) && age >= 0 && age <= limit;
  });
}

const normalized = (value: string | null | undefined) => (value ?? '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase();

/** A marker represents a place in an outbreak, even if several feeds repeat it. */
export function deduplicatePoints(points: MapPoint[]): MapPoint[] {
  const seen = new Set<string>();
  const ids = new Set<string>();
  return points.filter(point => {
    const key = JSON.stringify([
      point.outbreak_id || point.alert_id, point.disease_id || normalized(point.disease_label),
      point.country_code, normalized(point.admin_area), point.published_date, point.status,
      point.host_type ?? 'unknown', normalized(point.host_species),
    ]);
    if (ids.has(point.alert_id) || seen.has(key)) return false;
    ids.add(point.alert_id);
    seen.add(key);
    return true;
  });
}

/** Shared count and map list scope, including the default 30-day window. */
export function visibleSituationPoints(points: MapPoint[], asOf: string, filters: Filters = DEFAULT_FILTERS): MapPoint[] {
  return deduplicatePoints(sortPoints(filterPoints(points, filters, asOf), asOf));
}

/** Stable outbreak/place identity, independent of the reporting channel or date. */
export function situationPlaceKey(point: MapPoint): string {
  return JSON.stringify([
    point.outbreak_id || point.alert_id,
    point.disease_id || normalized(point.disease_label),
    point.country_code.toUpperCase(), normalized(point.admin_area),
    point.host_type ?? 'unknown', normalized(point.host_species),
  ]);
}

/** Filter complete history first, then draw the latest matching report of each place. */
export function latestMatchingPlaces(points: MapPoint[], asOf: string, filters: Filters = DEFAULT_FILTERS): MapPoint[] {
  const places = new Map<string, MapPoint>();
  for (const point of sortPoints(filterPoints(points, filters, asOf), asOf)) {
    const key = situationPlaceKey(point);
    if (!places.has(key)) places.set(key, point);
  }
  return [...places.values()];
}

export type AlertGroup = SituationAlertRecord & { reports: SituationAlertRecord[] };

/**
 * Merge reports of the same figures and reporting period, regardless of when
 * each feed published them. Explicit year-to-date totals may span successive
 * cut-off dates; other figures retain their exact cut-off date. Keep every
 * original report and use the latest one for the displayed facts. Filter before
 * grouping so source and time facets also scope the retained report links.
 */
export function deduplicateAlerts(alerts: SituationAlertRecord[], preferredId?: string): AlertGroup[] {
  const groups = new Map<string, AlertGroup>();
  const ids = new Set<string>();
  for (const alert of alerts) {
    if (ids.has(alert.alert_id)) continue;
    ids.add(alert.alert_id);
    const hasFigures = alert.cases != null || alert.deaths != null;
    const resolution = resolveReportingPeriod(alert);
    const period = resolution.period;
    const periodKey = resolution.invalid ? ['invalid-period', alert.alert_id]
      : period?.kind === 'cumulative' ? ['cumulative', period.start]
      : period?.kind === 'incident' ? ['incident', period.start, period.end]
      : ['as-of', alert.as_of || alert.published_date, resolution.unresolved ?? ''];
    const evidence = hasFigures
      ? ['figures', alert.cases, alert.deaths]
      : alert.quote?.trim()
        ? ['quote', normalized(alert.quote)]
        : ['report', alert.notice_id || alert.url || alert.alert_id];
    const key = JSON.stringify([
      alert.outbreak_id || alert.notice_id || alert.alert_id,
      alert.disease_id || normalized(alert.disease_label), alert.country_code,
      normalized(alert.admin_area), alert.status,
      alert.host_type ?? 'unknown', normalized(alert.host_species), alert.count_unit ?? 'unknown',
      periodKey,
      // Sources can describe the same annual laboratory-confirmed total as
      // "total" or "confirmed". Suspected and probable counts stay separate.
      hasFigures && resolution.annual && (alert.count_basis === 'total' || alert.count_basis === 'confirmed')
        ? 'reported' : alert.count_basis,
      evidence,
    ]);
    const existing = groups.get(key);
    if (!existing) {
      groups.set(key, { ...alert, reports: [alert] });
    } else {
      const reports = [...existing.reports, alert];
      const newer = alert.published_date > existing.published_date;
      const preferredTie = alert.published_date === existing.published_date && alert.alert_id === preferredId;
      groups.set(key, { ...(newer || preferredTie ? alert : existing), reports });
    }
  }
  return [...groups.values()];
}

/** Points updated in the last 7 days first, then newest first. */
export function sortPoints<T extends FilterablePoint>(points: T[], asOf: string): T[] {
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

const GOLDEN_ANGLE = 2.399963;

/**
 * Points with exported coordinates sit where the report says; several at one
 * spot nudge apart slightly. Points without coordinates fan out around their
 * country anchor so none hides another.
 */
export function placePoints<T extends FilterablePoint>(
  points: T[],
  anchors: Record<string, [number, number]>,
): { placed: PlacedPoint<T>[]; unplaced: T[] } {
  const countrySeen = new Map<string, number>();
  const spotSeen = new Map<string, number>();
  const placed: PlacedPoint<T>[] = [];
  const unplaced: T[] = [];
  for (const point of points) {
    if (typeof point.lat === 'number' && typeof point.lng === 'number') {
      const key = `${point.lat.toFixed(2)},${point.lng.toFixed(2)}`;
      const index = spotSeen.get(key) ?? 0;
      spotSeen.set(key, index + 1);
      const radius = index === 0 ? 0 : 0.35 * Math.sqrt(index);
      const angle = index * GOLDEN_ANGLE;
      placed.push({ point, lng: point.lng + radius * Math.cos(angle), lat: point.lat + radius * Math.sin(angle) });
      continue;
    }
    const anchor = anchors[point.country_code];
    if (!anchor) {
      unplaced.push(point);
      continue;
    }
    const index = countrySeen.get(point.country_code) ?? 0;
    countrySeen.set(point.country_code, index + 1);
    const angle = index * GOLDEN_ANGLE;
    const radius = index === 0 ? 0 : 1.1 * Math.sqrt(index);
    placed.push({ point, lng: anchor[0] + radius * Math.cos(angle), lat: anchor[1] + radius * Math.sin(angle) });
  }
  return { placed, unplaced };
}

export function placeLabel(point: Pick<MapPoint, 'admin_area' | 'country_code'>, countryName: string): string {
  return point.admin_area ? `${point.admin_area}, ${countryName}` : countryName;
}

/** The world minus Antarctica: [west, north] to [east, south]. */
export const WORLD_BOUNDS: [[number, number], [number, number]] = [[-180, 84], [180, -58]];
export const WORLD_CENTER: [number, number] = [(WORLD_BOUNDS[0][0] + WORLD_BOUNDS[1][0]) / 2, (WORLD_BOUNDS[0][1] + WORLD_BOUNDS[1][1]) / 2];

const FIT_PADDING = 1.5;
const FIT_MIN_SPAN = 12;
const FIT_MAX_ZOOM = 8;

/**
 * Centre and zoom that frame every placed point in a viewport of the given
 * pixel size. The map is flat, so points on both sides of the antimeridian
 * span nearly 360 degrees and fall back to the whole-world view.
 */
export function fitView(
  placed: { lng: number; lat: number }[],
  width: number,
  height: number,
): { center: [number, number]; zoom: number } {
  const world = { center: WORLD_CENTER, zoom: 1 };
  if (!placed.length || width <= 0 || height <= 0) return world;
  let minLng = Infinity, maxLng = -Infinity, minLat = Infinity, maxLat = -Infinity;
  for (const { lng, lat } of placed) {
    minLng = Math.min(minLng, lng);
    maxLng = Math.max(maxLng, lng);
    minLat = Math.min(minLat, lat);
    maxLat = Math.max(maxLat, lat);
  }
  const spanLng = Math.max(maxLng - minLng, FIT_MIN_SPAN) * FIT_PADDING;
  const spanLat = Math.max(maxLat - minLat, FIT_MIN_SPAN) * FIT_PADDING;
  if (spanLng >= 180) return world;
  const lngRange = WORLD_BOUNDS[1][0] - WORLD_BOUNDS[0][0];
  const latRange = WORLD_BOUNDS[0][1] - WORLD_BOUNDS[1][1];
  const cover = Math.max(width / lngRange, height / latRange);
  const zoom = Math.min(FIT_MAX_ZOOM, Math.max(1, Math.min(width / (cover * spanLng), height / (cover * spanLat))));
  return { center: [(minLng + maxLng) / 2, (minLat + maxLat) / 2], zoom };
}

export const MAX_LANES = 6;
export const LANE_COLORS = ['#14b8a6', '#8b5cf6', '#ec4899', '#2563eb', '#84cc16', '#a16207'];
export const OTHER_LANE_COLOR = '#8d9aa8';

export interface Lane {
  key: string;
  color: string;
  other: boolean;
}

/**
 * One line in a row, in graph columns. `up` runs from the row top to the node,
 * `down` from the node to the row bottom, `through` from top to bottom and
 * bends from column `from` to column `to` in the bottom of the row.
 */
export interface GraphLine {
  lane: number;
  from: number;
  to: number;
  part: 'up' | 'down' | 'through';
}

export interface GraphRow<T> {
  alert: T;
  lane: number;
  node: number;
  lines: GraphLine[];
}

/**
 * Lays alerts out like a commit graph: newest first, one lane per country.
 * A lane only takes a column while its country is active, and the columns to
 * its right close the gap when it ends, so the graph is as wide as the number
 * of countries active at once. The focused country is ranked first for colour,
 * then busier countries. Past MAX_LANES the quietest countries share one grey lane.
 */
export function buildGraph<T extends { published_date: string; country_code: string }>(
  alerts: T[],
  focusCountry = '',
): { lanes: Lane[]; rows: GraphRow<T>[]; width: number } {
  const sorted = alerts
    .map((alert, index) => ({ alert, index }))
    .sort((a, b) => b.alert.published_date.localeCompare(a.alert.published_date) || a.index - b.index)
    .map(item => item.alert);

  const counts = new Map<string, number>();
  for (const alert of sorted) counts.set(alert.country_code, (counts.get(alert.country_code) ?? 0) + 1);
  const ranked = [...counts.keys()].sort(
    (a, b) => Number(b === focusCountry) - Number(a === focusCountry) || (counts.get(b) ?? 0) - (counts.get(a) ?? 0) || a.localeCompare(b),
  );

  const overflow = ranked.length > MAX_LANES;
  const own = overflow ? ranked.slice(0, MAX_LANES - 1) : ranked;
  const lanes: Lane[] = own.map((key, index) => ({ key, color: LANE_COLORS[index % LANE_COLORS.length], other: false }));
  if (overflow) lanes.push({ key: '*', color: OTHER_LANE_COLOR, other: true });

  const laneOf = new Map(own.map((key, index) => [key, index]));
  const otherLane = lanes.length - 1;
  const laneIndexes = sorted.map(alert => laneOf.get(alert.country_code) ?? otherLane);

  const last = lanes.map(() => -Infinity);
  laneIndexes.forEach((lane, row) => {
    last[lane] = row;
  });

  const active: number[] = [];
  let width = 1;
  const rows = sorted.map((alert, row): GraphRow<T> => {
    const lane = laneIndexes[row];
    const known = active.indexOf(lane);
    const started = known < 0;
    const node = started ? active.length : known;
    const ends = row === last[lane];
    const lines: GraphLine[] = [];
    active.forEach((other, col) => {
      if (other === lane) return;
      lines.push({ lane: other, from: col, to: ends && col > node ? col - 1 : col, part: 'through' });
    });
    if (!started) lines.push({ lane, from: node, to: node, part: ends ? 'up' : 'through' });
    else if (!ends) lines.push({ lane, from: node, to: node, part: 'down' });
    if (started && !ends) active.push(lane);
    else if (!started && ends) active.splice(node, 1);
    width = Math.max(width, node + 1, ...lines.map(line => Math.max(line.from, line.to) + 1));
    return { alert, lane, node, lines };
  });
  return { lanes, rows, width };
}
