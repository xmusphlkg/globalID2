import type { SourceSeriesMetadata } from './countryDataset';

export interface DiseaseDatasetSeriesEntry {
  disease_id: string;
  name_en: string;
  name_zh: string;
  dates: string[];
  cases: (number | null)[];
  weekly_equiv_cases: (number | null)[];
  deaths: (number | null)[];
  incidence_rates: (number | null)[];
  incidence_sources?: (string | null)[];
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
  available_series_count?: number;
  coverage_status?: string | null;
  selected_series_codes?: string[];
  metric_layers?: Record<string, string>;
  source_series?: SourceSeriesMetadata[];
}

export interface DiseaseDatasetMonthlyData {
  months: string[];
  cases: number[];
  deaths: number[];
}

export interface DiseaseDataset {
  country_series?: Record<string, DiseaseDatasetSeriesEntry>;
  global_monthly?: DiseaseDatasetMonthlyData | null;
}

interface CompactDiseaseDatasetSeriesEntry {
  cc: string;
  n?: string;
  n_zh?: string;
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

interface CompactDiseaseDataset {
  v: number;
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
    return raw;
  }

  const sourceLabels = raw.sources ?? [];
  const countrySeries = Object.fromEntries(
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
        entry.cc,
        {
          disease_id: entry.cc,
          name_en: entry.n ?? entry.cc,
          name_zh: entry.n_zh ?? entry.n ?? entry.cc,
          dates,
          cases: entry.c ?? [],
          weekly_equiv_cases: entry.w ?? [],
          deaths: entry.d ?? [],
          incidence_rates: incidenceRates,
          incidence_sources: incidenceSources,
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
