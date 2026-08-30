import type { CountryDatasetSeriesEntry } from './countryDataset';

export type CurveSeries = CountryDatasetSeriesEntry;
export type CurveEntityType = 'disease' | 'country';
export type EpidemicMetric = 'weekly_equiv_cases' | 'cases' | 'historical_index' | 'trend_index' | 'deaths' | 'incidence_rates';
export type CurveSelectionMode = 'single' | 'multiple';
export type EpidemicAnalysisMode = 'monitor' | 'compare' | 'outbreak';

export interface HistoricalReference {
  expected: (number | null)[];
  lower: (number | null)[];
  upper: (number | null)[];
  index: (number | null)[];
  referenceCounts: number[];
  eligiblePointCount: number;
}

export interface ComparisonAssessment {
  level: 'direct' | 'conditional' | 'blocked';
  reasons: string[];
  commonWindow: DateWindow | null;
}

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
export const ANIMATION_POINT_LIMIT = 5_000;

export const METRIC_LABELS: Record<EpidemicMetric, { en: string; zh: string }> = {
  weekly_equiv_cases: { en: 'Weekly reported cases', zh: '周度报告病例数' },
  cases: { en: 'Source-period cases', zh: '来源期间病例数' },
  historical_index: { en: 'Observed / historical expected (%)', zh: '观察值／历史预期值（%）' },
  trend_index: { en: 'Trend index (series peak = 100)', zh: '趋势指数（各序列峰值 = 100）' },
  deaths: { en: 'Deaths', zh: '死亡数' },
  incidence_rates: { en: 'Cases per 100k per source period', zh: '每个来源期间每10万人病例数' },
};

export const INITIAL_CURVE_VIEW_STATE: EpidemicCurveViewState = {
  metric: DEFAULT_METRIC,
  dateWindow: null,
};

export function getCurveLineSampling(
  values: (number | null)[],
  analysisMode: EpidemicAnalysisMode,
): 'lttb' | undefined {
  if (analysisMode === 'outbreak') return undefined;
  return values.some((value) => value == null || !Number.isFinite(value))
    ? undefined
    : 'lttb';
}

export function buildStableSeriesColorMap(ids: string[], colors: string[]) {
  const palette = colors.length > 0 ? colors : ['currentColor'];
  return new Map<string, string>(
    Array.from(new Set(ids))
      .sort((left, right) => left.localeCompare(right))
      .map((id, index): [string, string] => [id, palette[index % palette.length]])
  );
}

export function getMetricValues(
  item: CurveSeries,
  metric: EpidemicMetric
): (number | null)[] {
  if (metric === 'incidence_rates') return item.incidence_rates ?? [];
  if (metric === 'historical_index') {
    return buildHistoricalReference(
      item.dates ?? [],
      item.cases ?? [],
      getSeriesGranularity(item)
    ).index;
  }
  if (metric === 'trend_index') return normalizeTrendIndex(item.cases ?? []);
  if (metric === 'weekly_equiv_cases') {
    return supportsWeeklyEquivalent(item) ? item.weekly_equiv_cases : [];
  }
  return item[metric] ?? [];
}

export function normalizeTrendIndex(values: (number | null)[]): (number | null)[] {
  const finiteValues = values.filter(
    (value): value is number => value != null && Number.isFinite(value)
  );
  if (finiteValues.length === 0) return values.map(() => null);

  const peak = Math.max(...finiteValues);
  if (peak <= 0) {
    return values.map((value) => (
      value == null || !Number.isFinite(value) ? null : 0
    ));
  }
  return values.map((value) => (
    value == null || !Number.isFinite(value)
      ? null
      : Number(((value / peak) * 100).toFixed(2))
  ));
}

function quantile(values: number[], probability: number) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const position = (sorted.length - 1) * probability;
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return sorted[lower];
  const weight = position - lower;
  return sorted[lower] + ((sorted[upper] - sorted[lower]) * weight);
}

function isoWeek(date: Date) {
  const target = new Date(Date.UTC(
    date.getUTCFullYear(),
    date.getUTCMonth(),
    date.getUTCDate()
  ));
  const weekday = target.getUTCDay() || 7;
  target.setUTCDate(target.getUTCDate() + 4 - weekday);
  const yearStart = new Date(Date.UTC(target.getUTCFullYear(), 0, 1));
  return Math.ceil((((target.getTime() - yearStart.getTime()) / 86_400_000) + 1) / 7);
}

function referenceSlot(date: Date, granularity: string) {
  const normalized = normalizeTemporalGranularity(granularity);
  if (normalized === 'annual') return 'annual';
  if (normalized === 'quarterly') return `q${Math.floor(date.getUTCMonth() / 3) + 1}`;
  if (normalized === 'monthly') return `m${date.getUTCMonth() + 1}`;
  if (normalized === 'weekly') return `w${isoWeek(date)}`;
  if (normalized === 'daily') {
    return `d${date.getUTCMonth() + 1}-${date.getUTCDate()}`;
  }
  return null;
}

export function buildHistoricalReference(
  dates: string[],
  values: (number | null)[],
  granularity: string,
  minimumHistory = 5,
): HistoricalReference {
  const expected = dates.map((): number | null => null);
  const lower = dates.map((): number | null => null);
  const upper = dates.map((): number | null => null);
  const index = dates.map((): number | null => null);
  const referenceCounts = dates.map(() => 0);
  const history = new Map<string, Array<{ year: number; value: number }>>();

  dates.forEach((dateText, pointIndex) => {
    const date = new Date(`${dateText}T00:00:00Z`);
    const value = values[pointIndex];
    if (!Number.isFinite(date.getTime())) return;
    const slot = referenceSlot(date, granularity);
    if (!slot) return;

    const year = date.getUTCFullYear();
    const prior = (history.get(slot) ?? [])
      .filter((item) => item.year < year)
      .map((item) => item.value);
    referenceCounts[pointIndex] = prior.length;
    if (prior.length >= minimumHistory) {
      const median = quantile(prior, 0.5);
      const low = quantile(prior, 0.25);
      const high = quantile(prior, 0.75);
      expected[pointIndex] = median;
      lower[pointIndex] = low;
      upper[pointIndex] = high;
      if (value != null && Number.isFinite(value) && median != null && median > 0) {
        index[pointIndex] = Number(((value / median) * 100).toFixed(2));
      }
    }

    if (value != null && Number.isFinite(value)) {
      history.set(slot, [...(history.get(slot) ?? []), { year, value }]);
    }
  });

  return {
    expected,
    lower,
    upper,
    index,
    referenceCounts,
    eligiblePointCount: expected.filter((value) => value != null).length,
  };
}

export function insertMissingPeriodBreaks(
  dates: string[],
  values: (number | null)[],
  granularity: string,
  pointGranularities: Array<string | null | undefined> = [],
) {
  if (dates.length < 2 || dates.length !== values.length) return { dates, values };
  const normalized = normalizeTemporalGranularity(granularity);
  const outputDates: string[] = [];
  const outputValues: (number | null)[] = [];

  const effectiveGranularity = (index: number) => {
    const pointGranularity = normalizeTemporalGranularity(pointGranularities[index]);
    return pointGranularity === 'unknown' ? normalized : pointGranularity;
  };

  const hasGap = (left: Date, right: Date, leftGranularity: string, rightGranularity: string) => {
    if (leftGranularity !== rightGranularity) {
      return ![leftGranularity, rightGranularity].some((value) => (
        value === 'unknown' || value === 'mixed'
      ));
    }
    const activeGranularity = leftGranularity;
    const elapsedDays = (right.getTime() - left.getTime()) / 86_400_000;
    const monthDifference = ((right.getUTCFullYear() - left.getUTCFullYear()) * 12)
      + right.getUTCMonth() - left.getUTCMonth();
    if (activeGranularity === 'daily') return elapsedDays > 1.5;
    if (activeGranularity === 'weekly') return elapsedDays > 10.5;
    if (activeGranularity === 'monthly') return monthDifference > 1;
    if (activeGranularity === 'quarterly') return monthDifference > 3;
    if (activeGranularity === 'annual') {
      return right.getUTCFullYear() - left.getUTCFullYear() > 1;
    }
    return false;
  };

  dates.forEach((date, index) => {
    if (index > 0) {
      const previous = new Date(`${dates[index - 1]}T00:00:00Z`);
      const current = new Date(`${date}T00:00:00Z`);
      if (
        Number.isFinite(previous.getTime())
        && Number.isFinite(current.getTime())
        && hasGap(
          previous,
          current,
          effectiveGranularity(index - 1),
          effectiveGranularity(index),
        )
      ) {
        outputDates.push(new Date(
          previous.getTime() + ((current.getTime() - previous.getTime()) / 2)
        ).toISOString().slice(0, 10));
        outputValues.push(null);
      }
    }
    outputDates.push(date);
    outputValues.push(values[index] ?? null);
  });
  return { dates: outputDates, values: outputValues };
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

export function formatIncidenceMetricLabel(
  granularities: string[],
  lang: 'en' | 'zh',
) {
  const normalized = Array.from(new Set(
    granularities.map(normalizeTemporalGranularity)
  ));
  if (normalized.length !== 1 || normalized[0] === 'unknown') {
    return lang === 'zh'
      ? '各自来源期间每10万人病例数'
      : 'Cases per 100k per respective source period';
  }
  const units: Record<string, { en: string; zh: string }> = {
    daily: { en: 'day', zh: '日' },
    weekly: { en: 'week', zh: '周' },
    monthly: { en: 'month', zh: '月' },
    quarterly: { en: 'quarter', zh: '季度' },
    annual: { en: 'year', zh: '年' },
  };
  const unit = units[normalized[0]] ?? { en: 'source period', zh: '来源期间' };
  return lang === 'zh'
    ? `每${unit.zh}每10万人病例数`
    : `Cases per 100k per ${unit.en}`;
}

export function getSelectedSourceSeries(item: CurveSeries) {
  const selectedCodes = new Set(item.selected_series_codes ?? []);
  const sources = item.source_series ?? [];
  return selectedCodes.size > 0
    ? sources.filter((source) => selectedCodes.has(source.series_code ?? ''))
    : sources;
}

function strictTrailingProvisionalFrom(
  dates: string[] | undefined,
  statuses: string[] | undefined,
) {
  if (!dates || !statuses || dates.length === 0 || dates.length !== statuses.length) {
    return undefined;
  }
  let boundary: string | null = null;
  for (let index = dates.length - 1; index >= 0; index -= 1) {
    const status = String(statuses[index] ?? '').trim().toLowerCase();
    if (!['provisional', 'preliminary'].includes(status)) break;
    boundary = dates[index];
  }
  return boundary;
}

export function getEffectiveProvisionalFrom(item: CurveSeries) {
  const sources = getSelectedSourceSeries(item);
  if (sources.length === 0) return item.provisional_from ?? null;

  const recalculated = sources.map((source) => (
    strictTrailingProvisionalFrom(source.dates, source.point_quality_statuses)
  ));
  if (recalculated.some((boundary) => boundary === undefined)) {
    return item.provisional_from ?? null;
  }
  const boundaries = recalculated.filter(
    (boundary): boundary is string => typeof boundary === 'string'
  );
  return boundaries.length > 0 ? boundaries.sort()[0] : null;
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

function median(values: number[]) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[middle - 1] + sorted[middle]) / 2
    : sorted[middle];
}

export function calculateIncidenceFromReferencePopulation(
  reference: CurveSeries,
  dates: string[],
  cases: (number | null)[],
) {
  const estimatesByYear = new Map<number, number[]>();
  (reference.dates ?? []).forEach((date, index) => {
    const caseValue = reference.cases?.[index];
    const rateValue = reference.incidence_rates?.[index];
    if (caseValue == null || rateValue == null || caseValue <= 0 || rateValue <= 0) return;
    const population = (caseValue / rateValue) * 100_000;
    const year = Number.parseInt(date.slice(0, 4), 10);
    if (!Number.isFinite(year) || !Number.isFinite(population) || population <= 0) return;
    estimatesByYear.set(year, [...(estimatesByYear.get(year) ?? []), population]);
  });
  const annualPopulation = new Map<number, number>();
  estimatesByYear.forEach((values, year) => {
    const value = median(values);
    if (value != null) annualPopulation.set(year, value);
  });
  return dates.map((date, index) => {
    const caseValue = cases[index];
    if (caseValue == null || !Number.isFinite(caseValue)) return null;
    const year = Number.parseInt(date.slice(0, 4), 10);
    const population = annualPopulation.get(year);
    if (population == null || population <= 0) return null;
    return Number(((caseValue / population) * 100_000).toFixed(4));
  });
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
    ?? source.values.reduce<number>((total, value) => (
      total + (value != null && Number.isFinite(value) ? value : 0)
    ), 0);
  const incidenceRates = calculateIncidenceFromReferencePopulation(
    item,
    source.dates,
    source.values,
  );
  const recalculatedProvisionalFrom = strictTrailingProvisionalFrom(
    source.dates,
    source.point_quality_statuses,
  );

  return {
    ...item,
    dates: source.dates,
    cases: source.values,
    weekly_equiv_cases: normalizeTemporalGranularity(source.temporal_granularity) === 'weekly'
      ? source.values.map((value) => (
          value != null && Number.isFinite(value) ? Number(value) : null
        ))
      : [],
    point_granularities: source.values.map(() => source.temporal_granularity ?? null),
    point_series_codes: source.values.map(() => source.series_code ?? seriesCode),
    point_data_layers: source.values.map(() => 'source_series_selected'),
    deaths: [],
    incidence_rates: incidenceRates,
    incidence_sources: incidenceRates.map((value) => (
      value == null ? null : 'wpp_population_recalculated'
    )),
    total_cases: totalCases,
    total_deaths: 0,
    latest_cases: findLatestValue(source.values) ?? 0,
    latest_deaths: 0,
    period_granularity: source.temporal_granularity,
    provisional_from: recalculatedProvisionalFrom === undefined
      ? source.provisional_from
      : recalculatedProvisionalFrom,
    projection_policy: 'source_series_selected',
    selected_series_codes: [source.series_code ?? seriesCode],
  };
}

export function getOutbreakEligibility(item: CurveSeries) {
  const granularity = getSeriesGranularity(item);
  const sourceTimeBases = getSelectedSourceSeries(item)
    .map((source) => String(source.time_basis ?? '').trim().toLowerCase())
    .filter(Boolean);
  const timeBases = sourceTimeBases.length > 0
    ? sourceTimeBases
    : [String(item.time_basis ?? '').trim().toLowerCase()].filter(Boolean);
  const fineGrain = granularity === 'daily' || granularity === 'weekly';
  const onsetBased = timeBases.some((basis) => (
    basis.includes('onset')
    || basis.includes('symptom')
    || basis.includes('illness start')
    || basis.includes('发病')
  ));
  if (!fineGrain) {
    return { eligible: false, reason: 'requires_daily_or_weekly_data' as const };
  }
  if (!onsetBased) {
    return { eligible: false, reason: 'requires_onset_time_basis' as const };
  }
  return { eligible: true, reason: null };
}

function semanticValues(
  items: CurveSeries[],
  field: 'metric_type' | 'reporting_basis' | 'time_basis' | 'definition_version',
) {
  return new Set(items.flatMap((item) => seriesSemanticValues(item, field)));
}

function seriesSemanticValues(
  item: CurveSeries,
  field: 'metric_type' | 'reporting_basis' | 'time_basis' | 'definition_version' | 'comparability',
) {
  const sourceValues = getSelectedSourceSeries(item).map((source) => (
    String(source[field] ?? '').trim().toLowerCase()
  )).filter(Boolean);
  if (sourceValues.length > 0) return sourceValues;
  const fallback = String(item[field] ?? '').trim().toLowerCase();
  return fallback ? [fallback] : [];
}

function hasMissingSemantic(
  items: CurveSeries[],
  field: 'metric_type' | 'reporting_basis' | 'time_basis' | 'definition_version',
) {
  return items.some((item) => seriesSemanticValues(item, field).length === 0);
}

export function getCommonDateWindow(
  items: CurveSeries[],
  metric: EpidemicMetric = 'cases',
): DateWindow | null {
  if (items.length === 0) return null;
  const ranges = items.flatMap((item) => {
    const metricValues = getMetricValues(item, metric);
    const observedDates = (item.dates ?? []).filter((_, index) => {
      const value = metricValues[index];
      return value != null && Number.isFinite(value);
    });
    return observedDates.length > 0
      ? [{ startDate: observedDates[0], endDate: observedDates[observedDates.length - 1] }]
      : [];
  });
  if (ranges.length !== items.length) return null;
  const startDate = ranges.reduce(
    (latest, range) => range.startDate > latest ? range.startDate : latest,
    ranges[0].startDate
  );
  const endDate = ranges.reduce(
    (earliest, range) => range.endDate < earliest ? range.endDate : earliest,
    ranges[0].endDate
  );
  return startDate <= endDate ? { startDate, endDate } : null;
}

export function assessComparison(
  items: CurveSeries[],
  metric: EpidemicMetric = 'cases',
): ComparisonAssessment {
  const commonWindow = getCommonDateWindow(items, metric);
  if (items.length < 2) {
    return { level: 'conditional', reasons: ['select_at_least_two'], commonWindow };
  }

  const reasons = new Set<string>();
  const comparability = new Set(items.flatMap((item) => {
    const values = seriesSemanticValues(item, 'comparability');
    return values.length > 0 ? values : ['unknown'];
  }));
  if (comparability.has('not_comparable')) reasons.add('source_not_comparable');
  if (comparability.has('conditional')) reasons.add('source_conditional');
  if (comparability.has('unknown')) {
    reasons.add('comparability_unknown');
  }
  if (new Set(items.map(getSeriesGranularity)).size > 1) {
    reasons.add('mixed_granularity');
  }
  if (items.some((item) => getSeriesGranularity(item) === 'unknown')) {
    reasons.add('granularity_unknown');
  }
  if (semanticValues(items, 'metric_type').size > 1) reasons.add('metric_mismatch');
  if (semanticValues(items, 'reporting_basis').size > 1) reasons.add('reporting_basis_mismatch');
  if (semanticValues(items, 'time_basis').size > 1) reasons.add('time_basis_mismatch');
  if (semanticValues(items, 'definition_version').size > 1) reasons.add('definition_mismatch');
  if (hasMissingSemantic(items, 'metric_type')) reasons.add('metric_unknown');
  if (hasMissingSemantic(items, 'reporting_basis')) reasons.add('reporting_basis_unknown');
  if (hasMissingSemantic(items, 'time_basis')) reasons.add('time_basis_unknown');
  if (hasMissingSemantic(items, 'definition_version')) reasons.add('definition_unknown');
  if (!commonWindow) reasons.add('no_common_window');

  const allDirect = comparability.size === 1
    && comparability.has('direct');
  const level = reasons.has('source_not_comparable') || reasons.has('no_common_window')
    ? 'blocked'
    : reasons.size === 0 && allDirect
      ? 'direct'
      : 'conditional';
  return { level, reasons: [...reasons], commonWindow };
}

export function clipToDateWindow<T>(
  dates: string[],
  values: T[],
  window: DateWindow | null,
) {
  if (!window) return { dates, values };
  const clippedDates: string[] = [];
  const clippedValues: Array<T | null> = [];
  dates.forEach((date, index) => {
    if (date < window.startDate || date > window.endDate) return;
    clippedDates.push(date);
    clippedValues.push(values[index] ?? null);
  });
  return { dates: clippedDates, values: clippedValues };
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
