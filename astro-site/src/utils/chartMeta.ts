export interface SourceSummary {
  country_code?: string;
  country_name?: string;
  scope?: string;
  label?: string;
  url?: string;
  type?: string;
  cadence?: string;
  description?: string;
}

export interface SourceInfoGroup {
  country_code?: string;
  country_name?: string;
  sources?: SourceSummary[];
}

export interface DownloadEntry {
  json_path?: string;
  csv_path?: string;
  generated_at?: string;
  record_count?: number;
  date_range?: { start?: string; end?: string };
  source_info?: SourceInfoGroup | SourceInfoGroup[] | { sources?: SourceSummary[] };
}

export interface ChartSourceMeta {
  label: string;
  href?: string;
  sources?: Array<{
    label: string;
    href?: string;
  }>;
  note?: string;
  downloadHref?: string;
  coverage?: string;
  updatedAt?: string;
  rowCount?: number;
}

function formatDate(value?: string | null) {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toISOString().slice(0, 10);
}

export function flattenSourceEntries(rawSourceInfo?: DownloadEntry['source_info']) {
  const sourceGroups = Array.isArray(rawSourceInfo)
    ? rawSourceInfo
    : rawSourceInfo
      ? [rawSourceInfo]
      : [];

  return sourceGroups.flatMap((group) => {
    const nested = Array.isArray(group?.sources) ? group.sources : [];
    if (nested.length > 0) {
      return nested.map((source) => ({
        ...source,
        country_code: source.country_code ?? group.country_code,
        country_name: source.country_name ?? group.country_name,
      }));
    }
    return [group as SourceSummary];
  });
}

export function buildChartSourceMeta(entry?: DownloadEntry | null, fallbackLabel = 'Official public-health sources'): ChartSourceMeta | null {
  if (!entry) return null;

  const sources = flattenSourceEntries(entry.source_info).filter((source) => source.label || source.scope || source.url);
  const primary = sources[0];
  const sourceLinks = sources
    .map((source) => {
      const label = source.label ?? source.scope;
      if (!label) return null;
      return {
        label,
        href: source.url,
      };
    })
    .filter(Boolean) as Array<{ label: string; href?: string }>;
  const sourceLabels = sourceLinks.map((source) => source.label);

  let label = fallbackLabel;
  if (sourceLabels.length > 0) {
    const head = sourceLabels.slice(0, 2).join('; ');
    const extraCount = Math.max(0, sourceLabels.length - 2);
    label = extraCount > 0 ? `${head} + ${extraCount} more` : head;
  }

  const coverage = (entry.date_range?.start || entry.date_range?.end)
    ? `${entry.date_range?.start ?? '—'} → ${entry.date_range?.end ?? '—'}`
    : undefined;
  const updatedAt = formatDate(entry.generated_at);

  const noteParts: string[] = [];
  if (primary?.description) noteParts.push(primary.description);

  return {
    label,
    href: primary?.url,
    sources: sourceLinks,
    note: noteParts.join(' · ') || undefined,
    downloadHref: entry.csv_path ?? entry.json_path,
    coverage,
    updatedAt,
    rowCount: entry.record_count,
  };
}
