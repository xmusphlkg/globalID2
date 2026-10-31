/** Join a situation place to the public catalogue. No filesystem and no React. */

import { toSeoSlug } from '../../lib/seo.ts';
import type { CountryGlance, DiseaseGlance, SeriesGlance, SiteIndex } from '../../utils/situationTypes.ts';

const SPARK_LIMIT = 16;

export interface SeriesInput {
  cases?: Array<number | null> | null;
  dates?: Array<string | null> | null;
  slug?: string | null;
}

export interface CountryInput {
  code: string;
  name_en: string;
  name_zh?: string | null;
  diseaseCount: number;
  latestDate?: string | null;
  page: boolean;
  researchCount?: number;
  researchUrl?: string | null;
  series: Record<string, SeriesInput>;
}

export interface DiseaseInput {
  id: string;
  slug: string;
  name_en: string;
  name_zh?: string | null;
  researchCount?: number;
  researchUrl?: string | null;
}

export function seriesKey(countryCode: string, diseaseId: string): string {
  return `${countryCode.toUpperCase()}:${diseaseId.toUpperCase()}`;
}

function finitePoints(
  cases: Array<number | null> | null | undefined,
  dates: Array<string | null> | null | undefined,
): Array<{ value: number; date: string | null }> {
  if (!Array.isArray(cases) || !Array.isArray(dates)) return [];
  const points: Array<{ value: number; date: string | null }> = [];
  for (let index = 0; index < cases.length; index += 1) {
    const value = cases[index];
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    const date = dates[index];
    points.push({ value, date: typeof date === 'string' ? date : null });
  }
  return points;
}

/** Newest reported value, the change against the point before it, and a short tail for a sparkline. */
export function seriesGlance(
  cases: Array<number | null> | null | undefined,
  dates: Array<string | null> | null | undefined,
): SeriesGlance | null {
  const points = finitePoints(cases, dates);
  if (points.length === 0) return null;
  let latestIndex = -1;
  for (let index = points.length - 1; index >= 0; index -= 1) {
    if (points[index].value > 0) {
      latestIndex = index;
      break;
    }
  }
  if (latestIndex < 0) latestIndex = points.length - 1;
  const latest = points[latestIndex];
  const previous = latestIndex > 0 ? points[latestIndex - 1] : null;
  const changePct = previous && previous.value > 0
    ? Math.round((((latest.value - previous.value) / previous.value) * 100) * 10) / 10
    : null;
  const glance: SeriesGlance = { value: latest.value };
  if (latest.date) glance.date = latest.date;
  if (changePct != null) glance.changePct = changePct;
  const spark = points.slice(-SPARK_LIMIT).map(point => point.value);
  if (spark.length > 0) glance.spark = spark;
  return glance;
}

function withPrefix(prefix: string, path: string | null | undefined): string | undefined {
  if (!path || !path.startsWith('/')) return undefined;
  return `${prefix}${path}`;
}

export function buildSituationSite(input: {
  countries: CountryInput[];
  diseases: DiseaseInput[];
  /** `${code}:${slug}` for published country-disease pages. Code is lowercase. */
  curveKeys: Iterable<string>;
  /** `ISO:DISEASE` pairs that should carry a sparkline. */
  sparkPairs: Iterable<string>;
  prefix?: string;
}): SiteIndex {
  const prefix = input.prefix ?? '';
  const curves = new Set(input.curveKeys);
  const sparks = new Set([...input.sparkPairs].map(pair => pair.toUpperCase()));
  const countries: Record<string, CountryGlance> = {};
  const series: Record<string, SeriesGlance> = {};

  for (const country of input.countries) {
    const code = country.code.toUpperCase();
    if (!/^[A-Z]{2}$/.test(code)) continue;
    const glance: CountryGlance = {
      name_en: country.name_en || code,
      researchCount: country.researchCount ?? 0,
      seriesCount: country.diseaseCount,
    };
    if (country.name_zh) glance.name_zh = country.name_zh;
    if (country.latestDate) glance.latestDate = country.latestDate;
    if (country.page) glance.href = `${prefix}/countries/${code.toLowerCase()}/`;
    const researchHref = withPrefix(prefix, country.researchUrl);
    if (researchHref) glance.researchHref = researchHref;
    countries[code] = glance;

    for (const [diseaseId, entry] of Object.entries(country.series)) {
      const built = seriesGlance(entry.cases, entry.dates);
      if (!built) continue;
      const key = seriesKey(code, diseaseId);
      const slug = toSeoSlug(entry.slug);
      if (slug && curves.has(`${code.toLowerCase()}:${slug}`)) {
        built.curveHref = `${prefix}/countries/${code.toLowerCase()}/diseases/${slug}/`;
      }
      if (!sparks.has(key)) delete built.spark;
      series[key] = built;
    }
  }

  const diseases: Record<string, DiseaseGlance> = {};
  for (const disease of input.diseases) {
    const id = disease.id.toUpperCase();
    const slug = toSeoSlug(disease.slug);
    const glance: DiseaseGlance = {
      slug,
      name_en: disease.name_en || id,
      researchCount: disease.researchCount ?? 0,
    };
    if (disease.name_zh) glance.name_zh = disease.name_zh;
    if (slug) glance.href = `${prefix}/diseases/${slug}/`;
    const researchHref = withPrefix(prefix, disease.researchUrl);
    if (researchHref) glance.researchHref = researchHref;
    diseases[id] = glance;
  }

  return { countries, diseases, series };
}
