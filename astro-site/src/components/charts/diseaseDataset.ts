import { decodeCompactIncidence, type SourceSeriesMetadata } from './countryDataset';
import { localizedRegionName } from '../../utils/i18n';

export interface DiseaseDatasetSeriesEntry {
  recorded_at_as_of?: string | null;
  recorded_history_complete?: boolean | null;
  disease_id: string;
  name_en: string;
  name_zh: string;
  name_fr?: string;
  dates: string[];
  cases: (number | null)[];
  weekly_equiv_cases: (number | null)[];
  deaths: (number | null)[];
  incidence_rates: (number | null)[];
  incidence_sources?: (string | null)[];
  point_granularities?: (string | null)[];
  point_series_codes?: (string | null)[];
  point_data_layers?: (string | null)[];
  total_cases: number;
  total_deaths?: number;
  provisional_from?: string | null;
  metric_type?: string | null;
  reporting_basis?: string | null;
  time_basis?: string | null;
  comparability?: string | null;
  definition_version?: string | null;
  data_layer?: string;
  projection_policy?: string;
  loss_risk?: string | null;
  period_granularity?: string | null;
  period_granularity_basis?: "source" | "date_pattern" | "unknown";
  available_series_count?: number;
  coverage_status?: string | null;
  selected_series_codes?: string[];
  metric_layers?: Record<string, string>;
  source_series?: SourceSeriesMetadata[];
}

export interface DiseaseDatasetMonthlyData {
  months: string[];
  cases: (number | null)[];
  deaths: (number | null)[];
  coverage?: {
    scope: string;
    jurisdiction_universe_size: number;
    excluded_weekly_jurisdictions?: number;
    excluded_unknown_jurisdictions?: number;
    inferred_monthly_jurisdictions?: number;
    cases_reported_jurisdictions: number[];
    deaths_reported_jurisdictions: number[];
    cases_incompatible_months?: boolean[];
    deaths_incompatible_months?: boolean[];
  };
}

export interface DiseaseDataset {
  country_series?: Record<string, DiseaseDatasetSeriesEntry>;
  global_monthly?: DiseaseDatasetMonthlyData | null;
}

interface CompactDiseaseDatasetSeriesEntry {
  cc: string;
  n?: string;
  n_zh?: string;
  n_fr?: string;
  tc?: number;
  td?: number;
  x: number[];
  c: (number | null)[];
  w: (number | null)[];
  d: (number | null)[];
  pf?: string | null;
  mt?: string | null;
  rb?: string | null;
  tb?: string | null;
  cmp?: string | null;
  dv?: string | null;
  ri?: number[];
  rv?: number[];
  rs?: Array<number | null>;
  point_granularities?: (string | null)[];
  point_series_codes?: (string | null)[];
  point_data_layers?: (string | null)[];
  data_layer?: string;
  projection_policy?: string;
  loss_risk?: string | null;
  period_granularity?: string | null;
  period_granularity_basis?: "source" | "date_pattern" | "unknown";
  available_series_count?: number;
  coverage_status?: string | null;
  selected_series_codes?: string[];
  metric_layers?: Record<string, string>;
  source_series?: SourceSeriesMetadata[];
}

interface CompactDiseaseDataset {
  v: number;
  meta?: { recorded_at_as_of?: string | null; recorded_history_complete?: boolean | null };
  dates: string[];
  sources?: string[];
  series: CompactDiseaseDatasetSeriesEntry[];
  monthly?: DiseaseDatasetMonthlyData | null;
}

const cache = new Map<string, Promise<DiseaseDataset>>();

function isCompactDiseaseDataset(value: unknown): value is CompactDiseaseDataset {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<CompactDiseaseDataset>;
  return candidate.v === 1 && Array.isArray(candidate.dates) && Array.isArray(candidate.series);
}

function normalizeDiseaseDataset(raw: DiseaseDataset | CompactDiseaseDataset): DiseaseDataset {
  if (!isCompactDiseaseDataset(raw)) {
    if (!raw.country_series) return raw;
    return {
      ...raw,
      country_series: Object.fromEntries(Object.entries(raw.country_series).map(([id, entry]) => [
        id,
        { ...entry, recorded_at_as_of: entry.recorded_at_as_of ?? null, recorded_history_complete: entry.recorded_history_complete ?? null, name_fr: localizedRegionName('fr', id, { en: entry.name_en, zh: entry.name_zh, fr: entry.name_fr }) },
      ])),
    };
  }

  const sourceLabels = raw.sources ?? [];
  const countrySeries = Object.fromEntries(
    raw.series.map((entry) => {
      const dates = entry.x.map((index) => raw.dates[index] ?? '');
      const { rates: incidenceRates, sources: incidenceSources } =
        decodeCompactIncidence(dates.length, entry, sourceLabels);

      return [
        entry.cc,
        {
          disease_id: entry.cc,
          recorded_at_as_of: raw.meta?.recorded_at_as_of ?? null,
          recorded_history_complete: raw.meta?.recorded_history_complete ?? null,
          name_en: entry.n ?? entry.cc,
          name_zh: entry.n_zh ?? entry.n ?? entry.cc,
          name_fr: localizedRegionName('fr', entry.cc, { en: entry.n, zh: entry.n_zh, fr: entry.n_fr }),
          dates,
          cases: entry.c ?? [],
          weekly_equiv_cases: entry.w ?? [],
          deaths: entry.d ?? [],
          incidence_rates: incidenceRates,
          incidence_sources: incidenceSources,
          point_granularities: entry.point_granularities ?? [],
          point_series_codes: entry.point_series_codes ?? [],
          point_data_layers: entry.point_data_layers ?? [],
          total_cases: entry.tc ?? 0,
          total_deaths: entry.td ?? 0,
          provisional_from: entry.pf,
          metric_type: entry.mt,
          reporting_basis: entry.rb,
          time_basis: entry.tb,
          comparability: entry.cmp,
          definition_version: entry.dv,
          data_layer: entry.data_layer,
          projection_policy: entry.projection_policy,
          loss_risk: entry.loss_risk,
          period_granularity: entry.period_granularity,
          period_granularity_basis: entry.period_granularity_basis,
          available_series_count: entry.available_series_count ?? 0,
          coverage_status: entry.coverage_status,
          selected_series_codes: entry.selected_series_codes ?? [],
          metric_layers: entry.metric_layers ?? {},
          source_series: entry.source_series ?? [],
        } satisfies DiseaseDatasetSeriesEntry,
      ];
    })
  );

  return {
    country_series: countrySeries,
    global_monthly: raw.monthly ?? null,
  };
}

export function loadDiseaseDataset(dataUrl?: string | null): Promise<DiseaseDataset> {
  if (!dataUrl) {
    return Promise.resolve({});
  }

  const cached = cache.get(dataUrl);
  if (cached) return cached;

  const request = fetch(dataUrl)
    .then(async (response) => {
      if (!response.ok) {
        throw new Error(`Failed to load disease dataset: ${response.status}`);
      }
      const raw = await response.json();
      return normalizeDiseaseDataset(raw as DiseaseDataset | CompactDiseaseDataset);
    })
    .catch((error) => {
      cache.delete(dataUrl);
      throw error;
    });

  cache.set(dataUrl, request);
  return request;
}
