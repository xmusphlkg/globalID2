import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Lang } from './i18n';
import type { InternationalNotice } from './international';

/**
 * Cross-signal between an authority notice and the local surveillance snapshot
 * it talks about: for ``(country, disease)`` of a WHO/ECDC entry, the newest
 * local point and its change against the previous period.
 */

export const SNAPSHOT_DATA_DIR = resolve('./src/data/countries');

export interface LocalSnapshot {
  countryCode: string;
  diseaseId: string;
  latestValue: number | null;
  latestDate: string | null;
  previousValue: number | null;
  changePct: number | null;
}

interface CountryDiseaseSeries {
  cases?: Array<number | null>;
  deaths?: Array<number | null>;
  dates?: string[];
  slug?: string | null;
}

interface CountryBundle {
  series: Record<string, CountryDiseaseSeries> | null;
  name_en: string | null;
  name_zh: string | null;
}

const recordCache = new Map<string, CountryBundle>();

function countryBundle(countryCode: string): CountryBundle {
  const safe = /^[A-Za-z0-9_-]+$/.test(countryCode) ? countryCode.toLowerCase() : '';
  if (!safe) return { series: null, name_en: null, name_zh: null };
  const cached = recordCache.get(safe);
  if (cached) return cached;
  const bundle: CountryBundle = { series: null, name_en: null, name_zh: null };
  const path = resolve(SNAPSHOT_DATA_DIR, `${safe}.json`);
  if (existsSync(path)) {
    try {
      const record = JSON.parse(readFileSync(path, 'utf-8')) as {
        disease_series?: Record<string, CountryDiseaseSeries>;
        country_name_en?: string | null;
        country_name_zh?: string | null;
      };
      bundle.series = record.disease_series ?? null;
      bundle.name_en = record.country_name_en ?? null;
      bundle.name_zh = record.country_name_zh ?? null;
    } catch {
      // A malformed record simply behaves like a jurisdiction without data.
    }
  }
  recordCache.set(safe, bundle);
  return bundle;
}

function jurisdictionName(code: string, locale: Lang): string {
  const bundle = countryBundle(code);
  if (locale === 'zh') return bundle.name_zh || bundle.name_en || code;
  return bundle.name_en || code;
}

function has(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Newest local point (plus the one before it) for one country + disease. */
export function readLocalSnapshot(
  countryCode: string,
  diseaseId: string,
  unit: 'cases' | 'deaths' = 'cases',
): LocalSnapshot | null {
  const series = countryBundle(countryCode)?.series ?? null;
  const entry = series?.[diseaseId.toUpperCase()] ?? series?.[diseaseId] ?? null;
  if (!entry) return null;
  const values = unit === 'deaths' ? entry.deaths : entry.cases;
  const dates = entry.dates;
  if (!Array.isArray(values) || !Array.isArray(dates)) return null;

  // Case arrays are ordered oldest -> newest. Reporting often ends in trailing
  // zeros (no cases filed yet), so we take the most recent point that actually
  // reports cases and compare it with the usable point before it.
  let latest: { value: number; date: string | null } | null = null;
  let previous: { value: number; date: string | null } | null = null;
  for (let i = values.length - 1; i >= 0; i -= 1) {
    if (!has(values[i])) continue;
    const point = { value: values[i] as number, date: dates[i] ?? null };
    if (!latest) {
      if (point.value > 0) latest = point;
      continue;
    }
    previous = point;
    break;
  }
  if (!latest) return null;

  let changePct: number | null = null;
  if (previous && previous.value > 0) {
    changePct = ((latest.value - previous.value) / previous.value) * 100;
  }

  return {
    countryCode: countryCode.toUpperCase(),
    diseaseId: diseaseId.toUpperCase(),
    latestValue: latest.value,
    latestDate: latest.date,
    previousValue: previous?.value ?? null,
    changePct,
  };
}

const BADGE_LABEL = {
  en: 'Local snapshot',
  zh: '本地快照',
  fr: 'Instantané local',
} as const;

const VS_LABEL = {
  en: 'vs previous period',
  zh: '较上一周期',
  fr: 'vs période précédente',
} as const;

function formatValue(value: number, locale: Lang): string {
  return value.toLocaleString(locale === 'zh' ? 'zh-CN' : locale === 'fr' ? 'fr-FR' : 'en-US', {
    maximumFractionDigits: 1,
  });
}

function formatChange(value: number, locale: Lang): string {
  const digits = Math.abs(value) < 10 ? 1 : 0;
  const formatted = locale === 'fr'
    ? value.toLocaleString('fr-FR', { minimumFractionDigits: digits, maximumFractionDigits: digits })
    : value.toFixed(digits);
  return `${value > 0 ? '+' : ''}${formatted}%`;
}

/**
 * Human-readable badges attached to one notice: every country/disease pair the
 * notice mentions that also has a local series is rendered as
 * ``Local snapshot · US · 2026-08-14 · 1,234 (+8.2% vs previous period)``.
 */
const RECENCY_LIMIT_DAYS = 730;

function isRecent(date: string | null): boolean {
  if (!date) return false;
  const parsed = Date.parse(date);
  if (!Number.isFinite(parsed)) return false;
  return (Date.now() - parsed) / 86_400_000 <= RECENCY_LIMIT_DAYS;
}

export function noticeSnapshotBadges(
  notice: Pick<InternationalNotice, 'countries' | 'diseases'>,
  locale: Lang = 'en',
  options: { contextCodes?: string[]; limit?: number } = {},
): string[] {
  const { contextCodes = [], limit = 3 } = options;
  const badges: string[] = [];
  const seen = new Set<string>();
  const diseases = notice.diseases ?? [];

  const push = (code: string, diseaseId: string, fallbackName: string, allowStale = false) => {
    if (badges.length >= limit) return;
    const key = `${code.toUpperCase()}:${diseaseId.toUpperCase()}`;
    if (seen.has(key)) return;
    seen.add(key);
    const snapshot = readLocalSnapshot(code, diseaseId);
    if (!snapshot || snapshot.latestValue == null) return;
    // Jurisdictions the notice names are cross-referenced even when their local
    // series ended years ago (the date is printed, so nothing is implied);
    // page-context snapshots must be fresh enough to be worth showing.
    if (!allowStale && !isRecent(snapshot.latestDate)) return;
    const parts = [BADGE_LABEL[locale], fallbackName || jurisdictionName(code, locale)];
    if (snapshot.latestDate) parts.push(snapshot.latestDate);
    parts.push(formatValue(snapshot.latestValue, locale));
    if (snapshot.changePct != null && Number.isFinite(snapshot.changePct)) {
      parts.push(`${formatChange(snapshot.changePct, locale)} ${VS_LABEL[locale]}`);
    }
    badges.push(parts.join(' · '));
  };

  // 1) jurisdictions the notice itself names — always preferred, they carry the
  //    localized names straight from the notice payload.
  for (const country of notice.countries ?? []) {
    for (const disease of diseases) {
      const name = (locale === 'zh' ? country.name_zh : country.name_en) || country.code;
      push(country.code, disease.disease_id, name, true);
    }
  }
  // 2) jurisdictions the *page* is about (its reporting countries), so a notice
  //    on "cholera in the DRC" still cross-references local series where the
  //    disease is actually tracked.
  for (const code of contextCodes) {
    for (const disease of diseases) {
      push(code, disease.disease_id, '');
    }
  }
  return badges;
}
