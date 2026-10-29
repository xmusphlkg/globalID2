import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type {
  MonitoringProduct,
  OutbreakFile,
  SituationAlertRecord,
  SituationMapFile,
  SourceStatus,
  WeeklyChange,
} from './situationTypes.ts';

export const INTERNATIONAL_SCHEMA_VERSION = 'gids.situation_alerts.v1';
export const INTERNATIONAL_DIR = resolve('./src/data/situation/international');
export const INTERNATIONAL_MAP_PATH = resolve(INTERNATIONAL_DIR, 'map.json');

const SAFE_KEY = /^[A-Za-z0-9_-]+$/;
const PERIOD = /^\d{4}-W\d{2}$/;

/** Read a versioned export file; a missing, malformed or foreign-schema file reads as absent. */
function readVersioned<T>(path: string): T | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf-8'));
    return parsed && parsed.schema_version === INTERNATIONAL_SCHEMA_VERSION ? (parsed as T) : null;
  } catch {
    return null;
  }
}

export function readSituationMap(dir: string = INTERNATIONAL_DIR): SituationMapFile | null {
  const file = readVersioned<SituationMapFile>(resolve(dir, 'map.json'));
  return file && Array.isArray(file.points) ? file : null;
}

export function readOutbreak(outbreakId: string, dir: string = INTERNATIONAL_DIR): OutbreakFile | null {
  return SAFE_KEY.test(outbreakId) ? readVersioned<OutbreakFile>(resolve(dir, 'outbreaks', `${outbreakId}.json`)) : null;
}

export function readSources(dir: string = INTERNATIONAL_DIR): SourceStatus[] {
  return readVersioned<{ sources: SourceStatus[] }>(resolve(dir, 'sources.json'))?.sources ?? [];
}

export function readMonitoring(dir: string = INTERNATIONAL_DIR): MonitoringProduct[] {
  return readVersioned<{ products: MonitoringProduct[] }>(resolve(dir, 'monitoring.json'))?.products ?? [];
}

// --- entity shards ---

export interface AlertShard {
  schema_version: string;
  alert_count: number;
  alerts: SituationAlertRecord[];
}

function alertsFromShard(path: string): SituationAlertRecord[] {
  return readVersioned<AlertShard>(path)?.alerts ?? [];
}

/** Alerts exported for one GIDS disease id (``D002``). */
export function readAlertsByDisease(diseaseId: string, dir: string = INTERNATIONAL_DIR): SituationAlertRecord[] {
  return SAFE_KEY.test(diseaseId) ? alertsFromShard(resolve(dir, 'by-disease', `${diseaseId}.json`)) : [];
}

/** Alerts exported for one ISO jurisdiction (``CD``). */
export function readAlertsByCountry(countryCode: string, dir: string = INTERNATIONAL_DIR): SituationAlertRecord[] {
  return SAFE_KEY.test(countryCode)
    ? alertsFromShard(resolve(dir, 'by-country', `${countryCode.toUpperCase()}.json`))
    : [];
}

const COUNTRY_RECORD_DIR = resolve('./src/data/countries');

/** ISO codes a site country page stands for, harvested from its own record. */
function countryKeysForPage(pageSlug: string): string[] {
  if (!SAFE_KEY.test(pageSlug)) return [];
  const keys = [pageSlug];
  const recordPath = resolve(COUNTRY_RECORD_DIR, `${pageSlug.toLowerCase()}.json`);
  if (existsSync(recordPath)) {
    try {
      const record = JSON.parse(readFileSync(recordPath, 'utf-8')) as Record<string, unknown>;
      for (const field of ['iso2', 'iso3', 'alpha2', 'alpha3', 'code', 'country_code', 'iso_code']) {
        const value = record[field];
        if (typeof value === 'string' && value.trim()) keys.push(value.trim());
      }
    } catch {
      // A malformed record leaves the slug itself as the only key.
    }
  }
  return [...new Set(keys.map(key => key.toUpperCase()).filter(Boolean))];
}

/**
 * Alerts for one country *page*: the export is keyed by ISO code while the site
 * routes by slug (``jp``, ``us``), so every key the page stands for is tried
 * and the shards merged. Level-strict: a sub-national page (``us-ny``) only
 * gets alerts exported for its own code, never its parent country's.
 */
export function readAlertsForCountryPage(pageSlug: string, dir: string = INTERNATIONAL_DIR): SituationAlertRecord[] {
  const seen = new Set<string>();
  const alerts: SituationAlertRecord[] = [];
  for (const key of countryKeysForPage(pageSlug)) {
    for (const alert of readAlertsByCountry(key, dir)) {
      if (seen.has(alert.alert_id)) continue;
      seen.add(alert.alert_id);
      alerts.push(alert);
    }
  }
  return alerts;
}

/** Newest alerts first, capped at ``limit``. */
export function recentAlerts(alerts: SituationAlertRecord[] | null | undefined, limit = 8): SituationAlertRecord[] {
  return [...(alerts ?? [])]
    .sort((a, b) => b.published_date.localeCompare(a.published_date) || a.alert_id.localeCompare(b.alert_id))
    .slice(0, Math.max(1, limit));
}

// --- weeks and months ---

/** ISO week key (``2026-W43``) of a calendar day, or ``null`` for an unreadable date. */
export function isoWeekKey(day: string): string | null {
  const date = new Date(`${String(day).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7) + 3);
  const year = date.getUTCFullYear();
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const week = 1 + Math.round((((date.getTime() - jan4.getTime()) / 86_400_000) - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}

/** Calendar month (``YYYY-MM``) of an ISO week's Monday. */
export function isoWeekMonth(period: string): string | null {
  const match = /^(\d{4})-W(\d{2})$/.exec(period);
  if (!match) return null;
  const jan4 = new Date(Date.UTC(Number(match[1]), 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (Number(match[2]) - 1) * 7);
  return monday.toISOString().slice(0, 7);
}

/** Weeks with exported changes, newest first. */
export function listWeeks(dir: string = INTERNATIONAL_DIR): WeeklyChange[] {
  const index = readVersioned<{ periods: WeeklyChange[] }>(resolve(dir, 'weekly', 'index.json'));
  return (index?.periods ?? [])
    .filter(row => PERIOD.test(String(row.period_key)))
    .sort((a, b) => b.period_key.localeCompare(a.period_key));
}

export function readWeek(period: string, dir: string = INTERNATIONAL_DIR): WeeklyChange | null {
  return PERIOD.test(period) ? readVersioned<WeeklyChange>(resolve(dir, 'weekly', `${period}.json`)) : null;
}

/** Months holding at least one exported week, newest first. */
export function listMonths(dir: string = INTERNATIONAL_DIR): string[] {
  const months = listWeeks(dir).map(week => isoWeekMonth(week.period_key)).filter((month): month is string => Boolean(month));
  return [...new Set(months)].sort((a, b) => b.localeCompare(a));
}

export function weeksOfMonth(month: string, dir: string = INTERNATIONAL_DIR): WeeklyChange[] {
  return /^\d{4}-\d{2}$/.test(month) ? listWeeks(dir).filter(week => isoWeekMonth(week.period_key) === month) : [];
}

/** The alerts of one outbreak that were published in one ISO week, newest first. */
export function weekAlerts(outbreakId: string, period: string, dir: string = INTERNATIONAL_DIR): SituationAlertRecord[] {
  return recentAlerts(
    (readOutbreak(outbreakId, dir)?.alerts ?? []).filter(alert => isoWeekKey(alert.published_date) === period),
    500,
  );
}
