export interface SourceSeriesMetadata {
  series_code?: string;
  source_series_code?: string;
  source_system?: string;
  source_label?: string;
  metric_type?: string;
  reporting_basis?: string;
  temporal_granularity?: string;
  unit?: string;
  geography_key?: string;
  dimension_key?: string;
  mapping_relation?: string;
  comparability?: string;
  aggregation_policy?: string;
  availability_status?: string;
  missing_value_policy?: string;
  definition_version?: string;
  case_definition?: string;
  case_definition_uri?: string;
  observation_count?: number;
  total_value?: number;
  dates?: string[];
  values?: number[];
  quality_statuses?: string[];
}

export interface CountryDatasetSeriesEntry {
  disease_id: string;
  name_en: string;
  name_zh: string;
  category?: string;
  slug?: string;
  dates: string[];
  cases: number[];
  weekly_equiv_cases: number[];
  deaths: number[];
  incidence_rates: (number | null)[];
  incidence_sources?: (string | null)[];
  total_cases: number;
  total_deaths?: number;
  latest_cases?: number;
  latest_deaths?: number;
  incidence_rate?: number | null;
  mortality_rate?: number | null;
  data_layer?: string;
  projection_policy?: string;
  loss_risk?: string | null;
  period_granularity?: string | null;
  available_series_count?: number;
  coverage_status?: string | null;
  selected_series_codes?: string[];
  metric_layers?: Record<string, string>;
  source_series?: SourceSeriesMetadata[];
}

export interface CountryDatasetHeatmap {
  diseases: string[];
  disease_labels: string[];
  months: string[];
  z: number[][];
}

export interface CountryDataset {
  disease_series?: Record<string, CountryDatasetSeriesEntry>;
  heatmap?: CountryDatasetHeatmap | null;
}

export type SourceSeriesObservations = Record<string, SourceSeriesMetadata[]>;

interface CountrySourceSeriesDataset {
  v: number;
  series?: SourceSeriesObservations;
}

interface CompactCountryDatasetSeriesEntry {
  id: string;
  en: string;
  zh: string;
  cat?: string;
  slug?: string;
  tc?: number;
  td?: number;
  lc?: number;
  ld?: number;
  x: number[];
  c: number[];
  w: number[];
  d: number[];
  ri?: number[];
  rv?: number[];
  rs?: Array<number | null>;
  data_layer?: string;
  projection_policy?: string;
  loss_risk?: string | null;
  period_granularity?: string | null;
  available_series_count?: number;
  coverage_status?: string | null;
  selected_series_codes?: string[];
  metric_layers?: Record<string, string>;
  source_series?: SourceSeriesMetadata[];
}

interface CompactCountryDataset {
  v: number;
  dates: string[];
  sources?: string[];
  series: CompactCountryDatasetSeriesEntry[];
  heatmap?: {
    months: string[];
    disease_ids: string[];
    z: number[][];
  } | null;
}

const cache = new Map<string, Promise<CountryDataset>>();
const sourceSeriesCache = new Map<string, Promise<SourceSeriesObservations>>();
const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

function isCompactCountryDataset(value: unknown): value is CompactCountryDataset {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<CompactCountryDataset>;
  return candidate.v === 1 && Array.isArray(candidate.dates) && Array.isArray(candidate.series);
}

function normalizeCountryDataset(raw: CountryDataset | CompactCountryDataset): CountryDataset {
  if (!isCompactCountryDataset(raw)) {
    return raw;
  }

  const sourceLabels = raw.sources ?? [];
  const diseaseSeries = Object.fromEntries(
    raw.series.map((entry) => {
      const dates = entry.x.map((index) => raw.dates[index] ?? '');
      const incidenceRates = new Array<number | null>(dates.length).fill(null);
      const incidenceSources = new Array<string | null>(dates.length).fill(null);

      (entry.ri ?? []).forEach((pointIndex, sparseIndex) => {
        if (pointIndex < 0 || pointIndex >= dates.length) return;
        incidenceRates[pointIndex] = entry.rv?.[sparseIndex] ?? null;
        const sourceCode = entry.rs?.[sparseIndex];
        incidenceSources[pointIndex] = sourceCode == null ? null : (sourceLabels[sourceCode] ?? null);
      });

      return [
        entry.id,
        {
          disease_id: entry.id,
          name_en: entry.en,
          name_zh: entry.zh,
          category: entry.cat,
          slug: entry.slug,
          dates,
          cases: entry.c ?? [],
          weekly_equiv_cases: entry.w ?? [],
          deaths: entry.d ?? [],
          incidence_rates: incidenceRates,
          incidence_sources: incidenceSources,
          total_cases: entry.tc ?? 0,
          total_deaths: entry.td ?? 0,
          latest_cases: entry.lc ?? 0,
          latest_deaths: entry.ld ?? 0,
          data_layer: entry.data_layer,
          projection_policy: entry.projection_policy,
          loss_risk: entry.loss_risk,
          period_granularity: entry.period_granularity,
          available_series_count: entry.available_series_count ?? 0,
          coverage_status: entry.coverage_status,
          selected_series_codes: entry.selected_series_codes ?? [],
          metric_layers: entry.metric_layers ?? {},
          source_series: entry.source_series ?? [],
        } satisfies CountryDatasetSeriesEntry,
      ];
    })
  );

  const heatmap = raw.heatmap
    ? {
        diseases: raw.heatmap.disease_ids ?? [],
        disease_labels: (raw.heatmap.disease_ids ?? []).map((id) => diseaseSeries[id]?.name_en ?? id),
        months: raw.heatmap.months ?? [],
        z: raw.heatmap.z ?? [],
      }
    : null;

  return {
    disease_series: diseaseSeries,
    heatmap,
  };
}

interface LoadCountryDatasetOptions {
  timeoutMs?: number;
}

export function invalidateCountryDataset(dataUrl: string) {
  cache.delete(dataUrl);
}

export function loadCountrySourceSeries(
  dataUrl?: string | null,
  { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS }: LoadCountryDatasetOptions = {}
): Promise<SourceSeriesObservations> {
  if (!dataUrl) return Promise.resolve({});

  const cached = sourceSeriesCache.get(dataUrl);
  if (cached) return cached;

  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  const request = fetch(dataUrl, {
    signal: controller.signal,
    credentials: 'same-origin',
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Failed to load country source series: ${response.status}`);
      }
      const raw = await response.json() as CountrySourceSeriesDataset;
      return raw?.v === 1 && raw.series && typeof raw.series === 'object'
        ? raw.series
        : {};
    })
    .catch((error) => {
      sourceSeriesCache.delete(dataUrl);
      throw error;
    })
    .finally(() => globalThis.clearTimeout(timeoutId));

  sourceSeriesCache.set(dataUrl, request);
  return request;
}

export function loadCountryDataset(
  dataUrl?: string | null,
  { timeoutMs = DEFAULT_REQUEST_TIMEOUT_MS }: LoadCountryDatasetOptions = {}
): Promise<CountryDataset> {
  if (!dataUrl) {
    return Promise.resolve({});
  }

  const cached = cache.get(dataUrl);
  if (cached) return cached;

  const controller = new AbortController();
  const timeoutId = globalThis.setTimeout(() => controller.abort(), timeoutMs);
  const request = fetch(dataUrl, {
    signal: controller.signal,
    credentials: 'same-origin',
  })
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Failed to load country dataset: ${response.status}`);
      }
      const raw = await response.json();
      return normalizeCountryDataset(raw as CountryDataset | CompactCountryDataset);
    })
    .catch((error) => {
      cache.delete(dataUrl);
      throw error;
    })
    .finally(() => globalThis.clearTimeout(timeoutId));

  cache.set(dataUrl, request);
  return request;
}
