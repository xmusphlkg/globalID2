import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Lang } from './i18n';

export const INTERNATIONAL_SCHEMA_VERSION = 'gids.international_authority_events.v1';
export const INTERNATIONAL_DATA_PATH = resolve('./src/data/situation/international/latest.json');

export interface InternationalNoticeDisease {
  disease_id: string;
  name_en: string | null;
  name_zh: string | null;
  slug: string | null;
}

export interface InternationalNoticeCountry {
  code: string;
  name_en: string | null;
  name_zh: string | null;
}

export interface InternationalNotice {
  event_id: string;
  source_id: string;
  native_id: string;
  record_type: string;
  title_en: string | null;
  title_zh: string | null;
  summary_en: string | null;
  summary_zh: string | null;
  url: string;
  source_label_en: string | null;
  source_label_zh: string | null;
  agency_en: string | null;
  agency_zh: string | null;
  language: string | null;
  published_date: string | null;
  updated_at_source: string | null;
  iso_year: number | null;
  iso_week: number | null;
  iso_week_key: string | null;
  diseases: InternationalNoticeDisease[];
  countries: InternationalNoticeCountry[];
  retrieved_at: string | null;
  fingerprint: string | null;
  // Peer-reviewed AI enrichment; null until a reviewer model accepted it.
  title_fr?: string | null;
  summary_fr?: string | null;
  event_type?: string | null;
  risk_level?: RiskLevel | null;
  risk_rationale?: string | null;
  ai?: NoticeAIProvenance | null;
}

export type RiskLevel = 'low' | 'moderate' | 'high' | 'very_high';

export interface NoticeAIProvenance {
  author_model: string | null;
  reviewer_model: string | null;
  /** ``single_model`` means one model drafted and audited the output in two separate calls. */
  review_mode: 'cross_model' | 'single_model' | null;
  verdict: string | null;
  rounds?: number | null;
  score?: number | null;
  prompt_version: string | null;
}

export interface InternationalSourceStatus {
  source_id: string;
  label_en: string | null;
  label_zh: string | null;
  endpoint: string | null;
  homepage: string | null;
  redistribution: string | null;
  status: string;
  event_count: number;
  undated_records: number;
  error: string | null;
}

export interface InternationalLatest {
  schema_version: string;
  generated_at: string | null;
  window: { since_days: number | null; since_date: string | null; as_of: string | null };
  event_count: number;
  recent_event_count: number;
  recent: InternationalNotice[];
  iso_weeks: { iso_week_key: string; iso_year: number | null; iso_week: number | null; event_count: number }[];
  diseases: ({ disease_id: string; name_en: string | null; name_zh: string | null; event_count: number })[];
  countries: ({ code: string; name_en: string | null; name_zh: string | null; event_count: number })[];
  sources: InternationalSourceStatus[];
}

export function readInternationalLatest(path: string = INTERNATIONAL_DATA_PATH): InternationalLatest | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf-8'));
    return parsed && parsed.schema_version === INTERNATIONAL_SCHEMA_VERSION ? (parsed as InternationalLatest) : null;
  } catch {
    return null;
  }
}

export const INTERNATIONAL_DISEASE_DIR = resolve('./src/data/situation/international/by-disease');
export const INTERNATIONAL_COUNTRY_DIR = resolve('./src/data/situation/international/by-country');

export interface InternationalShard {
  schema_version: string;
  iso_years: string[];
  event_count: number;
  disease?: InternationalNoticeDisease | null;
  country?: InternationalNoticeCountry | null;
  events: InternationalNotice[];
}

function shardFromFile(path: string): InternationalShard | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf-8'));
    return parsed && parsed.schema_version === INTERNATIONAL_SCHEMA_VERSION
      ? (parsed as InternationalShard)
      : null;
  } catch {
    return null;
  }
}

/** Notices already exported for one GIDS disease id (``D002``). */
export function readInternationalByDisease(
  diseaseId: string,
  dir: string = INTERNATIONAL_DISEASE_DIR,
): InternationalShard | null {
  const safe = /^[A-Za-z0-9_-]+$/.test(diseaseId) ? diseaseId : '';
  return safe ? shardFromFile(resolve(dir, `${safe}.json`)) : null;
}

/** Notices already exported for one ISO jurisdiction (``CD``). */
export function readInternationalByCountry(
  countryCode: string,
  dir: string = INTERNATIONAL_COUNTRY_DIR,
): InternationalShard | null {
  const safe = /^[A-Za-z0-9_-]+$/.test(countryCode) ? countryCode.toUpperCase() : '';
  return safe ? shardFromFile(resolve(dir, `${safe}.json`)) : null;
}

const COUNTRY_RECORD_DIR = resolve('./src/data/countries');

/** ISO codes a site country page stands for, harvested from its own record. */
function countryKeysForPage(pageSlug: string): string[] {
  const safe = /^[A-Za-z0-9_-]+$/.test(pageSlug) ? pageSlug.toLowerCase() : '';
  const keys = [safe];
  if (!safe) return [];
  const recordPath = resolve(COUNTRY_RECORD_DIR, `${safe}.json`);
  if (existsSync(recordPath)) {
    try {
      const record = JSON.parse(readFileSync(recordPath, 'utf-8')) as Record<string, unknown>;
      for (const field of ['iso2', 'iso3', 'alpha2', 'alpha3', 'code', 'country_code', 'iso_code']) {
        const value = record[field];
        if (typeof value === 'string' && value.trim()) keys.push(value.trim());
      }
    } catch {
      // A missing or malformed record simply leaves the slug itself as the key.
    }
  }
  return [...new Set(keys.map(key => key.toUpperCase()).filter(Boolean))];
}

/**
 * Notices for one country *page*: the export is keyed by ISO code while the
 * site routes by slug (``jp``, ``us``), so every key the page itself stands for
 * is tried in turn and their shards merged. Resolution stays level-strict: a
 * sub-national page (``us-ny``, ``au-act``) only ever gets notices exported for
 * its own code, never its parent country's.
 */
export function readInternationalForCountryPage(
  pageSlug: string,
  dir: string = INTERNATIONAL_COUNTRY_DIR,
): InternationalNotice[] {
  const seen = new Set<string>();
  const notices: InternationalNotice[] = [];
  for (const key of countryKeysForPage(pageSlug)) {
    const shard = readInternationalByCountry(key, dir);
    for (const notice of shard?.events ?? []) {
      if (seen.has(notice.event_id)) continue;
      seen.add(notice.event_id);
      notices.push(notice);
    }
  }
  return notices;
}

/** Most recent notices of a shard, newest first, capped at ``limit``. */
export function recentNotices(
  source: InternationalShard | InternationalNotice[] | null,
  limit = 8,
): InternationalNotice[] {
  const events = Array.isArray(source) ? source : source?.events ?? [];
  return [...events]
    .sort((a, b) => (b.published_date || '').localeCompare(a.published_date || ''))
    .slice(0, Math.max(1, limit));
}

export function noticeTitle(notice: InternationalNotice, locale: Lang = 'en'): string {
  if (locale === 'zh') return notice.title_zh || notice.title_en || '';
  if (locale === 'fr') return notice.title_fr || notice.title_en || '';
  return notice.title_en || notice.title_zh || '';
}

export function noticeSummary(notice: InternationalNotice, locale: Lang = 'en'): string | null {
  if (locale === 'zh') return notice.summary_zh ?? notice.summary_en;
  if (locale === 'fr') return notice.summary_fr ?? notice.summary_en;
  return notice.summary_en;
}

// --- events and peer-reviewed weekly digests ---

export const INTERNATIONAL_DIR = resolve('./src/data/situation/international');

export interface SituationEventRecord {
  event_id: string;
  disease_id: string;
  disease_name_en: string | null;
  disease_name_zh: string | null;
  disease_slug: string | null;
  country_codes: string[];
  first_published: string;
  last_published: string;
  notice_count: number;
  notice_ids: string[];
  source_ids: string[];
  risk_level: RiskLevel | null;
  status: 'active' | 'quiet';
}

export interface DigestPoint {
  text_en: string;
  text_zh: string | null;
  text_fr: string | null;
  notice_ids: string[];
}

export interface SituationDigestRecord {
  period_key: string;
  headline_en: string | null;
  headline_zh: string | null;
  headline_fr: string | null;
  points: DigestPoint[];
  notice_count: number;
  generated_at: string | null;
  ai: NoticeAIProvenance & { score?: number | null };
}

function readVersioned<T>(path: string): T | null {
  if (!existsSync(path)) return null;
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf-8'));
    return parsed && parsed.schema_version === INTERNATIONAL_SCHEMA_VERSION ? (parsed as T) : null;
  } catch {
    return null;
  }
}

export function readSituationEvents(dir: string = INTERNATIONAL_DIR): SituationEventRecord[] {
  return readVersioned<{ events: SituationEventRecord[] }>(resolve(dir, 'events.json'))?.events ?? [];
}

/** Published digest periods, newest first. */
export function listDigestPeriods(dir: string = INTERNATIONAL_DIR): string[] {
  const index = readVersioned<{ periods: { period_key: string }[] }>(resolve(dir, 'digests', 'index.json'));
  return (index?.periods ?? [])
    .map(row => row.period_key)
    .filter(key => /^\d{4}-W\d{2}$/.test(key))
    .sort((a, b) => b.localeCompare(a));
}

export function readDigest(period: string, dir: string = INTERNATIONAL_DIR): SituationDigestRecord | null {
  if (!/^\d{4}-W\d{2}$/.test(period)) return null;
  return readVersioned<SituationDigestRecord>(resolve(dir, 'digests', `${period}.json`));
}

/** Notices of one ISO week from the year archive, newest first. */
export function readWeekNotices(period: string, dir: string = INTERNATIONAL_DIR): InternationalNotice[] {
  const match = /^(\d{4})-W\d{2}$/.exec(period);
  if (!match) return [];
  const archive = readVersioned<{ events: InternationalNotice[] }>(resolve(dir, 'archive', `${match[1]}.json`));
  return recentNotices((archive?.events ?? []).filter(row => row.iso_week_key === period), 500);
}

/** Calendar month (``YYYY-MM``) of an ISO week's Monday. */
export function isoWeekMonth(period: string): string | null {
  const match = /^(\d{4})-W(\d{2})$/.exec(period);
  if (!match) return null;
  const year = Number(match[1]);
  const week = Number(match[2]);
  // ISO week 1 is the week holding 4 January.
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7);
  return monday.toISOString().slice(0, 7);
}

/** Months that hold at least one published weekly brief, newest first. */
export function listDigestMonths(dir: string = INTERNATIONAL_DIR): string[] {
  const months = listDigestPeriods(dir).map(isoWeekMonth).filter((month): month is string => Boolean(month));
  return [...new Set(months)].sort((a, b) => b.localeCompare(a));
}

/** Published weekly briefs whose week starts in ``month``, newest first. */
export function readMonthDigests(month: string, dir: string = INTERNATIONAL_DIR): SituationDigestRecord[] {
  if (!/^\d{4}-\d{2}$/.test(month)) return [];
  return listDigestPeriods(dir)
    .filter(period => isoWeekMonth(period) === month)
    .map(period => readDigest(period, dir))
    .filter((digest): digest is SituationDigestRecord => digest !== null);
}

export function digestHeadline(digest: SituationDigestRecord, locale: Lang = 'en'): string {
  if (locale === 'zh') return digest.headline_zh || digest.headline_en || '';
  if (locale === 'fr') return digest.headline_fr || digest.headline_en || '';
  return digest.headline_en || '';
}

export function digestPointText(point: DigestPoint, locale: Lang = 'en'): string {
  if (locale === 'zh') return point.text_zh || point.text_en;
  if (locale === 'fr') return point.text_fr || point.text_en;
  return point.text_en;
}

const RISK_ORDER: RiskLevel[] = ['low', 'moderate', 'high', 'very_high'];
export function riskRank(level: string | null | undefined): number {
  return RISK_ORDER.indexOf(level as RiskLevel);
}

export function noticeEntityName(
  entity: { name_en: string | null; name_zh: string | null },
  locale: Lang = 'en',
): string {
  return (locale === 'zh' ? entity.name_zh || entity.name_en : entity.name_en) || '';
}

export interface InternationalWeekGroup {
  iso_week_key: string;
  iso_year: number | null;
  iso_week: number | null;
  notices: InternationalNotice[];
}

export function groupNoticesByWeek(notices: InternationalNotice[]): InternationalWeekGroup[] {
  const groups = new Map<string, InternationalWeekGroup>();
  const undated: InternationalWeekGroup = { iso_week_key: 'undated', iso_year: null, iso_week: null, notices: [] };
  for (const notice of notices) {
    const key = notice.iso_week_key;
    if (!key) {
      undated.notices.push(notice);
      continue;
    }
    const group = groups.get(key) ?? {
      iso_week_key: key,
      iso_year: notice.iso_year,
      iso_week: notice.iso_week,
      notices: [] as InternationalNotice[],
    };
    group.notices.push(notice);
    groups.set(key, group);
  }
  const ordered = [...groups.values()].sort((a, b) => b.iso_week_key.localeCompare(a.iso_week_key));
  return undated.notices.length ? [...ordered, undated] : ordered;
}
