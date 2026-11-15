import { seriesGlance, seriesKey } from './site.ts';
import type { SeriesGlance, SiteIndex } from '../../utils/situationTypes.ts';

/** Reviewed notices and research links keep their own content version; the
 * surveillance join must never display observation values baked into HTML. */
export function currentSurveillanceSite(base: SiteIndex, meta: Record<string, any> | undefined, summaries: Record<string, Record<string, any>>): SiteIndex {
  const countries = Object.fromEntries(Object.entries(base.countries).map(([code, value]) => [code, { ...value, seriesCount: 0, latestDate: undefined }]));
  for (const row of meta?.countries ?? []) {
    const code = String(row.code ?? '').toUpperCase();
    if (!/^[A-Z]{2}(?:-[A-Z0-9]{1,8})?$/.test(code)) continue;
    countries[code] = { ...countries[code], name_en: row.name_en || row.name || countries[code]?.name_en || code,
      name_zh: row.name_zh || countries[code]?.name_zh, researchCount: countries[code]?.researchCount ?? 0,
      seriesCount: Number(row.disease_count ?? 0), latestDate: row.date_range?.end ?? undefined };
  }
  const series: Record<string, SeriesGlance> = {};
  for (const [diseaseId, summary] of Object.entries(summaries)) {
    for (const row of summary.reporting_countries ?? []) {
      const code = String(row.code ?? '').toUpperCase();
      if (!/^[A-Z]{2}(?:-[A-Z0-9]{1,8})?$/.test(code)) continue;
      const glance = seriesGlance(row.cases_tail, row.dates_tail);
      if (!glance) continue;
      const key = seriesKey(code, diseaseId);
      series[key] = { ...glance, ...(base.series[key]?.curveHref ? { curveHref: base.series[key].curveHref } : {}) };
    }
  }
  return { ...base, countries, series };
}
