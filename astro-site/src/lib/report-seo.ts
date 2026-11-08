import { buildSeoTitle, type SeoLocale } from './seo.ts';

export function reportCoverage(report: Record<string, any> | null | undefined): string {
  return report?.period_start && report?.period_end
    ? `${String(report.period_start).slice(0, 10)}–${String(report.period_end).slice(0, 10)}`
    : '';
}

// Used both by the route and the cheap pre-render publication gate.
export function reportOverviewTitle(report: Record<string, any>, country: string, locale: SeoLocale,
  countryData: Record<string, any> | null = null): string {
  const en = report.country_name_en ?? report.country_name ?? country.toUpperCase();
  const zh = report.country_name_local ?? report.country_name ?? en;
  const fr = report.country_name_fr ?? countryData?.country_name_fr ?? en;
  const coverage = reportCoverage(report);
  if (locale === 'zh') return `${zh}传染病监测报告${coverage ? ` ${coverage}` : ''} | GIDS`;
  if (locale === 'fr') return `${fr} — rapport de surveillance${coverage ? ` ${coverage}` : ''} | GIDS`;
  return buildSeoTitle(en, 'weekly infectious disease surveillance report', coverage);
}
