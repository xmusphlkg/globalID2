export interface ReportIndexEntry {
  country_code?: unknown;
  created_at?: unknown;
  id?: unknown;
  metadata?: {
    report_layout?: unknown;
    report_document_v4?: unknown;
  } | null;
  report_document_v4?: unknown;
  schema_version?: unknown;
  [key: string]: unknown;
}

export type ReportDetailLoader = (id: string) => unknown | null;

export interface PublishableReport {
  country: string;
  detail: ReportIndexEntry;
  diseaseSlugs: string[];
  id: string;
  summary: ReportIndexEntry;
}

export function isReportV4(value: unknown): value is ReportIndexEntry {
  if (!value || typeof value !== 'object') return false;

  const report = value as ReportIndexEntry;
  return report.metadata?.report_layout === 'report_v4'
    || Boolean(report.metadata?.report_document_v4)
    || Boolean(report.report_document_v4)
    || report.schema_version === 'report_v4.0';
}

export function normalizeReportRouteSegment(
  value: unknown,
  lowercase = false,
): string | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null;

  const segment = String(value).trim();
  if (!segment || segment === '.' || segment === '..' || /[\\/?#]/.test(segment)) return null;
  return lowercase ? segment.toLowerCase() : segment;
}

function reportDocument(report: unknown): Record<string, unknown> | null {
  if (!report || typeof report !== 'object') return null;
  const value = report as Record<string, any>;
  return value.report_document_v4 ?? value.metadata?.report_document_v4 ?? value;
}

export function reportDiseaseSlugs(report: unknown): string[] {
  const document = reportDocument(report);
  const directory = Array.isArray(document?.disease_directory)
    ? document.disease_directory
    : [];

  return [...new Set(
    directory
      .map((row: unknown) => (
        row && typeof row === 'object'
          ? normalizeReportRouteSegment((row as Record<string, unknown>).slug)
          : null
      ))
      .filter((slug: string | null): slug is string => slug !== null),
  )];
}

export function buildPublishableReports(
  value: unknown,
  loadReport: ReportDetailLoader,
): PublishableReport[] {
  if (!Array.isArray(value)) return [];

  const reports: PublishableReport[] = [];
  const seenRoutes = new Set<string>();

  for (const candidate of value) {
    if (!isReportV4(candidate)) continue;

    const country = normalizeReportRouteSegment(candidate.country_code, true);
    const id = normalizeReportRouteSegment(candidate.id);
    if (!country || !id) continue;

    const routeKey = `${country}/${id}`;
    if (seenRoutes.has(routeKey)) continue;

    const detail = loadReport(id);
    if (!isReportV4(detail)) continue;

    const detailCountry = normalizeReportRouteSegment(detail.country_code, true);
    const detailId = normalizeReportRouteSegment(detail.id);
    if (detailCountry !== country || detailId !== id) continue;

    seenRoutes.add(routeKey);
    reports.push({
      country,
      detail,
      diseaseSlugs: reportDiseaseSlugs(detail),
      id,
      summary: candidate,
    });
  }

  return reports;
}

export function reportArchiveCountries(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  return [...new Set(
    value
      .map((report: PublishableReport) => report?.country)
      .filter((country): country is string => typeof country === 'string' && country.length > 0),
  )].sort();
}

export function reportsForCountry(value: unknown, country: string | undefined): ReportIndexEntry[] {
  if (!Array.isArray(value)) return [];
  const normalizedCountry = normalizeReportRouteSegment(country, true);
  if (!normalizedCountry) return [];

  return value
    .filter((report: PublishableReport) => report?.country === normalizedCountry)
    .map((report: PublishableReport) => report.summary);
}
