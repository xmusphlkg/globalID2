import type { CountryDatasetSeriesEntry } from './countryDataset';

export type CurveSeries = CountryDatasetSeriesEntry;
export type CurveEntityType = 'disease' | 'country';
export type EpidemicMetric = 'weekly_equiv_cases' | 'cases' | 'deaths' | 'incidence_rates';

export interface DateWindow {
  startDate: string;
  endDate: string;
}

export interface EpidemicCurveViewState {
  metric: EpidemicMetric;
  dateWindow: DateWindow | null;
}

export type EpidemicCurveViewAction =
  | { type: 'metricChanged'; metric: EpidemicMetric }
  | { type: 'dateWindowChanged'; dateWindow: DateWindow }
  | { type: 'dateDomainChanged'; dates: string[] };

export const DEFAULT_METRIC: EpidemicMetric = 'cases';
export const MAX_ACTIVE_SERIES = 20;
export const ANIMATION_POINT_LIMIT = 5_000;

export const METRIC_LABELS: Record<EpidemicMetric, { en: string; zh: string }> = {
  weekly_equiv_cases: { en: 'Weekly reported cases', zh: '周度报告病例数' },
  cases: { en: 'Source-period cases', zh: '来源期间病例数' },
  deaths: { en: 'Deaths', zh: '死亡数' },
  incidence_rates: { en: 'Incidence rate (per 100k)', zh: '发病率（每10万）' },
};

export const INITIAL_CURVE_VIEW_STATE: EpidemicCurveViewState = {
  metric: DEFAULT_METRIC,
  dateWindow: null,
};

export function getMetricValues(
  item: CurveSeries,
  metric: EpidemicMetric
): (number | null)[] {
  if (metric === 'incidence_rates') return item.incidence_rates ?? [];
  if (metric === 'weekly_equiv_cases') {
    return supportsWeeklyEquivalent(item) ? item.weekly_equiv_cases : [];
  }
  return item[metric] ?? [];
}

export function normalizeTemporalGranularity(value: string | null | undefined) {
  const normalized = String(value ?? '').trim().toLowerCase();
  return normalized === 'yearly' ? 'annual' : normalized || 'unknown';
}

export function getSeriesGranularity(item: CurveSeries): string {
  const direct = normalizeTemporalGranularity(item.period_granularity);
  if (direct !== 'unknown') return direct;

  const selectedCodes = new Set(item.selected_series_codes ?? []);
  const candidates = (item.source_series ?? []).filter((source) => (
    selectedCodes.size === 0 || selectedCodes.has(source.series_code ?? '')
  ));
  const granularities = new Set(
    candidates
      .map((source) => normalizeTemporalGranularity(source.temporal_granularity))
      .filter((value) => value !== 'unknown')
  );
  return granularities.size === 1 ? [...granularities][0] : 'unknown';
}

export function formatTemporalGranularity(
  value: string | null | undefined,
  lang: 'en' | 'zh',
) {
  const granularity = normalizeTemporalGranularity(value);
  const labels: Record<string, { en: string; zh: string }> = {
    daily: { en: 'Daily', zh: '每日' },
    weekly: { en: 'Weekly', zh: '每周' },
    monthly: { en: 'Monthly', zh: '每月' },
    quarterly: { en: 'Quarterly', zh: '每季度' },
    annual: { en: 'Annual', zh: '每年' },
    mixed: { en: 'Mixed grain', zh: '混合粒度' },
    unknown: { en: 'Source cadence', zh: '按来源频率' },
  };
  return (labels[granularity] ?? {
    en: granularity.replaceAll('_', ' '),
    zh: granularity.replaceAll('_', ' '),
  })[lang];
}

export function supportsWeeklyEquivalent(item: CurveSeries): boolean {
  return getSeriesGranularity(item) === 'weekly'
    && (item.weekly_equiv_cases?.length ?? 0) > 0
    && item.weekly_equiv_cases.length === item.dates.length;
}

export function getSelectedSourceSeries(item: CurveSeries) {
  const selectedCodes = new Set(item.selected_series_codes ?? []);
  const sources = item.source_series ?? [];
  return selectedCodes.size > 0
    ? sources.filter((source) => selectedCodes.has(source.series_code ?? ''))
    : sources;
}

export function getSelectableSourceSeries(item: CurveSeries) {
  return (item.source_series ?? []).filter((source) => (
    Boolean(source.series_code)
    && Array.isArray(source.dates)
    && Array.isArray(source.values)
    && source.dates.length > 0
    && source.dates.length === source.values.length
  ));
}

export function hasPublicProjection(item: CurveSeries) {
  return Array.isArray(item.dates)
    && Array.isArray(item.cases)
    && item.dates.length > 0
    && item.dates.length === item.cases.length;
}

export function hasMixedSourceGranularities(item: CurveSeries) {
  const granularities = new Set(
    getSelectableSourceSeries(item)
      .map((source) => normalizeTemporalGranularity(source.temporal_granularity))
      .filter((value) => value !== 'unknown')
  );
  return granularities.size > 1;
}

export function selectSourceSeries(
  item: CurveSeries,
  seriesCode: string | null | undefined,
): CurveSeries {
  if (!seriesCode) return item;

  const source = getSelectableSourceSeries(item).find(
    (candidate) => candidate.series_code === seriesCode
  );
  if (!source || !source.dates || !source.values) return item;

  const totalCases = source.total_value
    ?? source.values.reduce((total, value) => total + (Number(value) || 0), 0);

  return {
    ...item,
    dates: source.dates,
    cases: source.values,
    weekly_equiv_cases: [],
    deaths: [],
    incidence_rates: [],
    incidence_sources: [],
    total_cases: totalCases,
    total_deaths: 0,
    latest_cases: findLatestValue(source.values) ?? 0,
    latest_deaths: 0,
    period_granularity: source.temporal_granularity,
    projection_policy: 'source_series_selected',
    selected_series_codes: [source.series_code ?? seriesCode],
  };
}

export function findLatestValue(values: (number | null)[]) {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] != null) return values[index];
  }
  return null;
}

export function collectDates(
  series: Record<string, CurveSeries>,
  activeIds: string[]
) {
  return Array.from(
    new Set(activeIds.flatMap((id) => series[id]?.dates ?? []))
  ).sort();
}

export function reconcileDateWindow(
  current: DateWindow | null,
  dates: string[]
): DateWindow | null {
  if (dates.length === 0) return null;

  const domainStart = dates[0];
  const domainEnd = dates[dates.length - 1];
  if (!current) {
    return { startDate: domainStart, endDate: domainEnd };
  }

  const startDate = current.startDate < domainStart
    ? domainStart
    : current.startDate > domainEnd
      ? domainEnd
      : current.startDate;
  const endDate = current.endDate > domainEnd
    ? domainEnd
    : current.endDate < domainStart
      ? domainStart
      : current.endDate;

  return startDate <= endDate
    ? { startDate, endDate }
    : { startDate: endDate, endDate: startDate };
}

export function dateWindowFromZoom(
  dates: string[],
  startPercent: number,
  endPercent: number,
  startValue?: string | number,
  endValue?: string | number
): DateWindow | null {
  if (dates.length === 0) return null;

  const start = Math.max(0, Math.min(100, startPercent));
  const end = Math.max(start, Math.min(100, endPercent));
  const lastIndex = dates.length - 1;
  const fallbackStartIndex = Math.min(lastIndex, Math.floor((start / 100) * lastIndex));
  const fallbackEndIndex = Math.min(lastIndex, Math.ceil((end / 100) * lastIndex));
  const timestamps = dates.map((date) => Date.parse(date));
  const startTimestamp = typeof startValue === 'number'
    ? startValue
    : Date.parse(startValue ?? '');
  const endTimestamp = typeof endValue === 'number'
    ? endValue
    : Date.parse(endValue ?? '');
  const resolvedStartIndex = timestamps.findIndex(
    (timestamp) => timestamp >= startTimestamp
  );
  let resolvedEndIndex = -1;
  for (let index = timestamps.length - 1; index >= 0; index -= 1) {
    if (timestamps[index] <= endTimestamp) {
      resolvedEndIndex = index;
      break;
    }
  }
  const startIndex = Number.isFinite(startTimestamp)
    ? (resolvedStartIndex >= 0 ? resolvedStartIndex : lastIndex)
    : fallbackStartIndex;
  const endIndex = Number.isFinite(endTimestamp)
    ? (resolvedEndIndex >= 0 ? resolvedEndIndex : 0)
    : fallbackEndIndex;

  return {
    startDate: dates[Math.min(startIndex, endIndex)],
    endDate: dates[Math.max(startIndex, endIndex)],
  };
}

export function sameDateWindow(
  left: DateWindow | null,
  right: DateWindow | null
) {
  return left?.startDate === right?.startDate
    && left?.endDate === right?.endDate;
}

export function epidemicCurveViewReducer(
  state: EpidemicCurveViewState,
  action: EpidemicCurveViewAction
): EpidemicCurveViewState {
  if (action.type === 'metricChanged') {
    return action.metric === state.metric
      ? state
      : { ...state, metric: action.metric };
  }

  const nextDateWindow = action.type === 'dateWindowChanged'
    ? action.dateWindow
    : reconcileDateWindow(state.dateWindow, action.dates);

  return sameDateWindow(state.dateWindow, nextDateWindow)
    ? state
    : { ...state, dateWindow: nextDateWindow };
}

export function buildTableRows(
  series: Record<string, CurveSeries>,
  activeIds: string[],
  metric: EpidemicMetric
) {
  const sortedDates = collectDates(series, activeIds);
  const lookups = new Map(
    activeIds.map((id) => {
      const item = series[id];
      const values = getMetricValues(item, metric);
      return [
        id,
        new Map((item.dates ?? []).map((date, index) => [date, values[index] ?? null])),
      ] as const;
    })
  );

  return sortedDates.map((date) => ({
    date,
    values: activeIds.map((id) => lookups.get(id)?.get(date) ?? null),
  }));
}
