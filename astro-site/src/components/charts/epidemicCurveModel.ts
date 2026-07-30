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

export const DEFAULT_METRIC: EpidemicMetric = 'weekly_equiv_cases';
export const MAX_ACTIVE_SERIES = 20;
export const ANIMATION_POINT_LIMIT = 5_000;

export const METRIC_LABELS: Record<EpidemicMetric, { en: string; zh: string }> = {
  weekly_equiv_cases: { en: 'Weekly Equivalent Cases', zh: '周等价病例数' },
  cases: { en: 'Cases', zh: '病例数' },
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
  if (metric === 'weekly_equiv_cases') return item.weekly_equiv_cases ?? [];
  return item[metric] ?? [];
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
