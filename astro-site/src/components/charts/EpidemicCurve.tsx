import { useEffect, useMemo, useState } from 'react';
import ChartFrame from './ChartFrame';
import CurveEntitySelector from './CurveEntitySelector';
import EpidemicCurvePlot from './EpidemicCurvePlot';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { useChartLanguage, useChartTheme } from './chartPreferences';
import { useCountryDataset, useCountrySourceSeries } from './useCountryDataset';
import { useEpidemicCurveState } from './useEpidemicCurveState';
import {
  METRIC_LABELS,
  aggregateToCompleteCalendarYears,
  assessAnnualAggregation,
  assessComparison,
  buildHistoricalReference,
  buildStableSeriesColorMap,
  clipToDateWindow,
  findLatestValue,
  formatIncidenceMetricLabel,
  formatTemporalGranularity,
  getEffectiveProvisionalFrom,
  getMetricValues,
  getSelectableSourceSeries,
  getSelectedSourceSeries,
  getSeriesGranularity,
  hasPublicProjection,
  getOutbreakEligibility,
  reconcileDateWindow,
  selectSourceSeries,
  type EpidemicAnalysisMode,
  type CurveEntityType,
  type CurveSeries,
} from './epidemicCurveModel';

interface Props {
  series?: Record<string, CurveSeries>;
  dataUrl?: string;
  title?: string;
  topN?: number;
  entityIds?: string[];
  entityType?: CurveEntityType;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
  sourceSeriesUrl?: string;
  initialLanguage?: 'en' | 'zh';
}

const SERIES_COLORS = [
  '#0072b2',
  '#d55e00',
  '#009e73',
  '#cc79a7',
  '#e69f00',
  '#56b4e9',
  '#7f3c8d',
  '#666666',
  '#bc5090',
  '#2f4b7c',
  '#4e79a7',
  '#f28e2b',
  '#59a14f',
  '#e15759',
  '#76b7b2',
  '#edc948',
  '#b07aa1',
  '#ff9da7',
  '#9c755f',
  '#6b8e23',
];
const EMPTY_SERIES: Record<string, CurveSeries> = {};

function formatCellValue(value: number | null | undefined, digits = 0) {
  if (value == null || Number.isNaN(value)) return '—';
  return digits > 0 ? value.toFixed(digits) : value.toLocaleString();
}

function formatMetadataValue(value: string | null | undefined) {
  const raw = String(value ?? '').trim();
  return raw ? raw.replaceAll('_', ' ') : null;
}

function uniqueValues(values: Array<string | null | undefined>) {
  return [...new Set(values.map(formatMetadataValue).filter((value): value is string => Boolean(value)))];
}

function trailingDateWindow(dates: string[], months: number) {
  if (dates.length === 0) return null;
  const endDate = dates[dates.length - 1];
  const end = new Date(`${endDate}T00:00:00Z`);
  if (!Number.isFinite(end.getTime())) return null;
  end.setUTCMonth(end.getUTCMonth() - months);
  const cutoff = end.toISOString().slice(0, 10);
  return {
    startDate: dates.find((date) => date >= cutoff) ?? dates[0],
    endDate,
  };
}

function provisionalDisplayScope(item: CurveSeries): 'none' | 'tail' | 'series' {
  const boundary = String(getEffectiveProvisionalFrom(item) ?? '').trim();
  if (!boundary) return 'none';
  const firstDate = item.dates.find((date) => Boolean(date));
  if (!firstDate) return 'none';
  return boundary <= firstDate ? 'series' : 'tail';
}

function eventDateFromValue(value: unknown): string | null {
  if (typeof value === 'string') {
    const match = value.match(/\d{4}-\d{2}-\d{2}/);
    return match?.[0] ?? null;
  }
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  for (const key of ['date', 'effective_from', 'valid_from', 'start_date']) {
    const candidate = eventDateFromValue(record[key]);
    if (candidate) return candidate;
  }
  return null;
}

function buildCurveEvents(
  item: CurveSeries,
  lang: 'en' | 'zh',
) {
  const firstDate = item.dates?.[0];
  const lastDate = item.dates?.[item.dates.length - 1];
  const events = getSelectedSourceSeries(item).flatMap((source) => {
    const candidates = [
      source.definition_effective_from ? {
        date: source.definition_effective_from,
        label: lang === 'zh'
          ? `定义 ${source.definition_version ?? ''} 生效`.trim()
          : `Definition ${source.definition_version ?? ''} effective`.trim(),
      } : null,
      eventDateFromValue(source.comparability_break) ? {
        date: eventDateFromValue(source.comparability_break) as string,
        label: lang === 'zh' ? '可比性断点' : 'Comparability break',
      } : null,
      source.valid_from && source.valid_from !== firstDate ? {
        date: source.valid_from,
        label: lang === 'zh' ? '来源序列开始' : 'Source series begins',
      } : null,
    ].filter((event): event is { date: string; label: string } => Boolean(event));
    return candidates;
  });
  return [...new Map(events
    .filter((event) => (
      (!firstDate || event.date >= firstDate) && (!lastDate || event.date <= lastDate)
    ))
    .map((event) => [`${event.date}:${event.label}`, event])).values()];
}

function comparisonReasonLabel(reason: string, lang: 'en' | 'zh') {
  const labels: Record<string, { en: string; zh: string }> = {
    select_at_least_two: { en: 'select at least two series', zh: '请至少选择两条序列' },
    source_not_comparable: { en: 'a source explicitly forbids direct comparison', zh: '至少一个来源明确标记为不可直接比较' },
    source_conditional: { en: 'source comparability is conditional', zh: '来源可比性为有条件可比' },
    comparability_unknown: { en: 'comparability is not established', zh: '可比性尚未确认' },
    mixed_granularity: { en: 'reporting cadences differ', zh: '报告频率不同' },
    granularity_unknown: { en: 'a reporting cadence is unknown', zh: '至少一个报告频率未知' },
    metric_mismatch: { en: 'metrics differ', zh: '指标定义不同' },
    metric_unknown: { en: 'a metric definition is missing', zh: '至少一个指标定义缺失' },
    reporting_basis_mismatch: { en: 'reporting bases differ', zh: '报告口径不同' },
    reporting_basis_unknown: { en: 'a reporting basis is missing', zh: '至少一个报告口径缺失' },
    time_basis_mismatch: { en: 'time bases differ', zh: '时间基准不同' },
    time_basis_unknown: { en: 'a time basis is missing', zh: '至少一个时间基准缺失' },
    definition_mismatch: { en: 'case-definition versions differ', zh: '病例定义版本不同' },
    definition_unknown: { en: 'a case-definition version is missing', zh: '至少一个病例定义版本缺失' },
    no_common_window: { en: 'there is no common observed time window', zh: '没有共同观测时间窗' },
  };
  return (labels[reason] ?? { en: reason.replaceAll('_', ' '), zh: reason.replaceAll('_', ' ') })[lang];
}

export default function EpidemicCurve({
  series: initialSeries,
  dataUrl,
  title,
  topN = 10,
  entityIds,
  entityType = 'disease',
  height = 420,
  sourceMeta = null,
  sourceSeriesUrl,
  initialLanguage = 'en',
}: Props) {
  const hasInitialSeries = Boolean(initialSeries && Object.keys(initialSeries).length > 0);
  const remoteDataset = useCountryDataset(dataUrl, !hasInitialSeries);
  const baseSeries = hasInitialSeries
    ? (initialSeries as Record<string, CurveSeries>)
    : (remoteDataset.data?.disease_series ?? EMPTY_SERIES);
  const sourceSeries = useCountrySourceSeries(sourceSeriesUrl, !hasInitialSeries);
  const [sourceSelectionById, setSourceSelectionById] = useState<Record<string, string>>({});
  const [analysisMode, setAnalysisMode] = useState<EpidemicAnalysisMode>('monitor');
  const [comparisonFrequencyMode, setComparisonFrequencyMode] = useState<
    'native' | 'seasonal_index' | 'annual_total'
  >('native');
  const enrichedSeries = useMemo(() => Object.fromEntries(
    Object.entries(baseSeries).map(([id, item]) => {
      const observations = sourceSeries.data[id] ?? [];
      if (observations.length === 0) return [id, item];

      const observationsByCode = new Map(
        observations.map((source) => [source.series_code ?? '', source])
      );
      const registeredCodes = new Set(
        (item.source_series ?? []).map((source) => source.series_code ?? '')
      );
      const mergedSources = (item.source_series ?? []).map((source) => ({
        ...source,
        ...(observationsByCode.get(source.series_code ?? '') ?? {}),
      }));
      observations.forEach((source) => {
        if (!registeredCodes.has(source.series_code ?? '')) mergedSources.push(source);
      });
      return [id, { ...item, source_series: mergedSources }];
    })
  ), [baseSeries, sourceSeries.data]);
  const effectiveSourceSelection = useMemo(() => Object.fromEntries(
    Object.entries(enrichedSeries).flatMap(([id, item]) => {
      const explicitSelection = sourceSelectionById[id];
      if (explicitSelection) return [[id, explicitSelection]];
      const selectableSources = getSelectableSourceSeries(item);
      if (!hasPublicProjection(item) && selectableSources.length === 1) {
        return [[id, selectableSources[0].series_code ?? '']];
      }
      return [];
    })
  ), [enrichedSeries, sourceSelectionById]);
  const series = useMemo(() => Object.fromEntries(
    Object.entries(enrichedSeries).map(([id, item]) => [
      id,
      selectSourceSeries(item, effectiveSourceSelection[id]),
    ])
  ), [effectiveSourceSelection, enrichedSeries]);
  const caseOnlyEntityIds = useMemo(
    () => Object.entries(effectiveSourceSelection)
      .filter(([, seriesCode]) => Boolean(seriesCode))
      .map(([id]) => id),
    [effectiveSourceSelection]
  );
  const theme = useChartTheme();
  const lang = useChartLanguage(initialLanguage);
  const curveState = useEpidemicCurveState({
    series,
    entityIds,
    topN,
    caseOnlyEntityIds,
    initialSelectionMode: 'single',
  });
  const colorById = useMemo(
    () => buildStableSeriesColorMap(Object.keys(series), SERIES_COLORS),
    [series]
  );

  const colors = useMemo(() => (
    theme === 'light'
      ? {
          font: '#556070',
          line: '#c9c2b8',
          grid: '#d9d2c7',
          hoverBg: '#fffdfa',
          hoverBorder: '#c9c2b8',
          hoverFont: '#162232',
          sliderBg: '#f7f3ec',
          fillerColor: 'rgba(78,121,167,0.14)',
          referenceBand: 'rgba(0,114,178,0.14)',
          provisionalArea: 'rgba(230,159,0,0.12)',
          title: '#162232',
        }
      : {
          font: '#a4b1c1',
          line: '#51667f',
          grid: '#344a64',
          hoverBg: '#17283a',
          hoverBorder: '#51667f',
          hoverFont: '#f3f6fb',
          sliderBg: '#122132',
          fillerColor: 'rgba(118,183,178,0.16)',
          referenceBand: 'rgba(86,180,233,0.16)',
          provisionalArea: 'rgba(230,159,0,0.14)',
          title: '#f3f6fb',
        }
  ), [theme]);

  useEffect(() => {
    const expectedSelectionMode = analysisMode === 'compare' ? 'multiple' : 'single';
    if (curveState.selectionMode !== expectedSelectionMode) {
      curveState.setSelectionMode(expectedSelectionMode);
    }
  }, [analysisMode, curveState.selectionMode, curveState.setSelectionMode]);

  const activeItems = useMemo(
    () => curveState.activeIds.map((id) => series[id]).filter(Boolean),
    [curveState.activeIds, series]
  );
  const seasonalComparisonAvailable = useMemo(() => (
    activeItems.length >= 2
    && activeItems.every((item) => (
      getMetricValues(item, 'historical_index').some((value) => value != null)
    ))
  ), [activeItems]);
  const annualAggregation = useMemo(
    () => assessAnnualAggregation(activeItems, curveState.metric),
    [activeItems, curveState.metric]
  );
  const annualAggregatesById = useMemo(() => new Map(
    curveState.activeIds.map((id) => {
      const item = series[id];
      return [id, aggregateToCompleteCalendarYears(
        item.dates,
        getMetricValues(item, curveState.metric),
        getSeriesGranularity(item),
      )];
    })
  ), [curveState.activeIds, curveState.metric, series]);
  const annualCommonWindow = useMemo(() => {
    const aggregates = curveState.activeIds.map((id) => annualAggregatesById.get(id));
    if (aggregates.some((aggregate) => !aggregate) || aggregates.length === 0) return null;
    const commonDates = aggregates.slice(1).reduce(
      (shared, aggregate) => new Set([...shared].filter((date) => aggregate!.dates.includes(date))),
      new Set(aggregates[0]!.dates),
    );
    const dates = [...commonDates].sort();
    return dates.length > 0 ? { startDate: dates[0], endDate: dates[dates.length - 1] } : null;
  }, [annualAggregatesById, curveState.activeIds]);
  const effectiveComparisonFrequencyMode = comparisonFrequencyMode === 'seasonal_index'
    && !seasonalComparisonAvailable
      ? 'native'
      : comparisonFrequencyMode === 'annual_total' && !annualAggregation.eligible
        ? 'native'
        : comparisonFrequencyMode;
  const effectiveMetric = analysisMode === 'compare'
    && effectiveComparisonFrequencyMode === 'seasonal_index'
      ? 'historical_index'
      : curveState.metric;
  useEffect(() => {
    if (comparisonFrequencyMode === 'seasonal_index' && !seasonalComparisonAvailable) {
      setComparisonFrequencyMode('native');
    }
    if (comparisonFrequencyMode === 'annual_total' && !annualAggregation.eligible) {
      setComparisonFrequencyMode('native');
    }
  }, [annualAggregation.eligible, comparisonFrequencyMode, seasonalComparisonAvailable]);
  const comparisonAssessment = useMemo(
    () => assessComparison(activeItems, effectiveMetric),
    [activeItems, effectiveMetric]
  );
  const comparisonBlocked = analysisMode === 'compare'
    && (
      comparisonAssessment.level === 'blocked'
      || (effectiveComparisonFrequencyMode === 'annual_total' && !annualCommonWindow)
    );
  const comparisonWindow = analysisMode === 'compare'
    ? effectiveComparisonFrequencyMode === 'annual_total'
      ? annualCommonWindow
      : comparisonAssessment.commonWindow
    : null;
  const outbreakEligibility = activeItems.length === 1
    ? getOutbreakEligibility(activeItems[0])
    : { eligible: false, reason: 'requires_single_series' as const };
  const outbreakBlocked = analysisMode === 'outbreak' && !outbreakEligibility.eligible;

  const lines = useMemo(() => (
    comparisonBlocked || outbreakBlocked ? [] : curveState.activeIds.map((id) => {
      const item = series[id];
      const selectedSources = getSelectedSourceSeries(item);
      const primarySource = selectedSources[0];
      const annualAggregate = effectiveComparisonFrequencyMode === 'annual_total'
        ? annualAggregatesById.get(id)
        : null;
      const sourceDates = annualAggregate?.dates ?? item.dates;
      const rawValues = annualAggregate?.values ?? getMetricValues(item, effectiveMetric);
      const clipped = clipToDateWindow(sourceDates, rawValues, comparisonWindow);
      const clippedPointGranularities = clipToDateWindow(
        sourceDates,
        annualAggregate ? [] : item.point_granularities ?? [],
        comparisonWindow
      ).values;
      const historicalReference = buildHistoricalReference(
        item.dates,
        item.cases ?? [],
        getSeriesGranularity(item)
      );
      const clippedExpected = clipToDateWindow(
        item.dates, historicalReference.expected, comparisonWindow
      ).values;
      const clippedLower = clipToDateWindow(
        item.dates, historicalReference.lower, comparisonWindow
      ).values;
      const clippedUpper = clipToDateWindow(
        item.dates, historicalReference.upper, comparisonWindow
      ).values;
      const provisionalScope = provisionalDisplayScope(item);
      const provisionalFrom = getEffectiveProvisionalFrom(item);
      return {
        id,
        name: lang === 'zh' ? item.name_zh : item.name_en,
        color: colorById.get(id) ?? SERIES_COLORS[0],
        dates: clipped.dates,
        values: clipped.values,
        granularity: annualAggregate ? 'annual' : getSeriesGranularity(item),
        pointGranularities: clippedPointGranularities,
        reportingBasis: primarySource?.reporting_basis ?? item.reporting_basis ?? undefined,
        timeBasis: primarySource?.time_basis ?? item.time_basis ?? undefined,
        sourceLabel: primarySource?.source_label,
        // A source-wide provisional declaration is communicated in the status
        // line. Painting the whole plotting area adds no temporal information.
        provisionalFrom: annualAggregate ? null : provisionalScope === 'tail' ? provisionalFrom : null,
        events: analysisMode === 'compare' ? [] : buildCurveEvents(item, lang),
        reference: analysisMode === 'monitor'
          && effectiveMetric === 'cases'
          && historicalReference.eligiblePointCount > 0
          ? {
              expected: clippedExpected,
              lower: clippedLower,
              upper: clippedUpper,
            }
          : undefined,
      };
    }).filter((line) => (
      line.values.length === line.dates.length
      && line.values.some((value) => value != null)
    ))
  ), [analysisMode, annualAggregatesById, colorById, comparisonBlocked, comparisonWindow, curveState.activeIds, effectiveComparisonFrequencyMode, effectiveMetric, lang, outbreakBlocked, series]);
  const plottedDates = useMemo(
    () => Array.from(new Set(lines.flatMap((line) => line.dates))).sort(),
    [lines]
  );
  const plottedDateWindow = useMemo(
    () => reconcileDateWindow(curveState.dateWindow, plottedDates),
    [curveState.dateWindow, plottedDates]
  );
  const sourceNotes = useMemo(() => (
    curveState.activeIds.flatMap((id) => {
      const item = series[id];
      const selectedSources = getSelectedSourceSeries(item);

      const labels = uniqueValues(
        selectedSources.map((source) => source.source_label ?? source.series_code)
      );
      const granularities = uniqueValues(selectedSources.length > 0
        ? selectedSources.map((source) => formatTemporalGranularity(source.temporal_granularity, lang))
        : [formatTemporalGranularity(item.period_granularity, lang)]);
      const bases = uniqueValues(selectedSources.length > 0
        ? selectedSources.map((source) => source.reporting_basis)
        : [item.reporting_basis]);
      const timeBases = uniqueValues(selectedSources.length > 0
        ? selectedSources.map((source) => source.time_basis)
        : [item.time_basis]);
      const definitions = uniqueValues(selectedSources.length > 0
        ? selectedSources.map((source) => source.case_definition ?? source.definition_version)
        : [item.definition_version]);
      const availability = uniqueValues(
        selectedSources.map((source) => source.availability_status)
      );
      const policies = uniqueValues(
        selectedSources.map((source) => source.aggregation_policy)
      );
      const comparability = uniqueValues(selectedSources.length > 0
        ? selectedSources.map((source) => source.comparability)
        : [item.comparability]);
      const missingPolicies = uniqueValues(
        selectedSources.map((source) => source.missing_value_policy)
      );

      const fragments = [
        labels.length > 0
          ? `${lang === 'zh' ? '来源' : 'Source'}: ${labels.join(' / ')}`
          : null,
        granularities.length > 0
          ? `${lang === 'zh' ? '报告粒度' : 'Grain'}: ${granularities.join(' / ')}`
          : null,
        bases.length > 0
          ? `${lang === 'zh' ? '报告口径' : 'Basis'}: ${bases.join(' / ')}`
          : null,
        timeBases.length > 0
          ? `${lang === 'zh' ? '时间基准' : 'Time basis'}: ${timeBases.join(' / ')}`
          : null,
        comparability.length > 0
          ? `${lang === 'zh' ? '可比性' : 'Comparability'}: ${comparability.join(' / ')}`
          : null,
        availability.length > 0
          ? `${lang === 'zh' ? '可用状态' : 'Availability'}: ${availability.join(' / ')}`
          : null,
        policies.length > 0
          ? `${lang === 'zh' ? '聚合策略' : 'Aggregation'}: ${policies.join(' / ')}`
          : null,
        missingPolicies.length > 0
          ? `${lang === 'zh' ? '缺失值规则' : 'Missing-value policy'}: ${missingPolicies.join(' / ')}`
          : null,
        definitions.length > 0
          ? `${lang === 'zh' ? '定义说明' : 'Definition'}: ${definitions.join(' / ')}`
          : null,
      ].filter((fragment): fragment is string => Boolean(fragment));

      if (fragments.length === 0) return [];
      return [{
        id,
        name: lang === 'zh' ? item.name_zh : item.name_en,
        fragments,
      }];
    })
  ), [curveState.activeIds, lang, series]);
  const activeGranularities = useMemo(
    () => new Set(lines.map((line) => line.granularity)),
    [lines]
  );
  const hiddenMetricSeriesCount = curveState.activeIds.length - lines.length;
  const facetByGranularity = activeGranularities.size > 1
    && (analysisMode === 'compare' || ['cases', 'deaths', 'incidence_rates'].includes(effectiveMetric));
  const chartHeight = facetByGranularity && typeof height === 'number'
    ? Math.max(height, (activeGranularities.size * 180) + 80)
    : height;
  const sourceControls = useMemo(() => curveState.activeIds.flatMap((id) => {
    const item = enrichedSeries[id];
    const sources = getSelectableSourceSeries(item);
    const publicProjectionAvailable = hasPublicProjection(item);
    if (sources.length < 2 && publicProjectionAvailable) return [];

    const selectedCode = effectiveSourceSelection[id] ?? '';
    const selectedSource = sources.find((source) => source.series_code === selectedCode);
    const projectionSource = getSelectedSourceSeries(item)[0];
    return [{
      id,
      item,
      sources,
      publicProjectionAvailable,
      selectedCode,
      displaySource: selectedSource ?? (publicProjectionAvailable ? projectionSource : undefined),
    }];
  }), [curveState.activeIds, effectiveSourceSelection, enrichedSeries]);
  const sourceOnlySelectionRequired = sourceControls.some((control) => (
    !control.publicProjectionAvailable && !control.selectedCode
  ));
  if (remoteDataset.loadError && Object.keys(series).length === 0) {
    return (
      <div className="chart-shell flex min-h-[160px] flex-col items-center justify-center gap-3 text-[rgb(var(--text-muted))] text-sm" role="alert">
        <span>
          {lang === 'zh'
            ? '图表数据加载失败或请求超时。'
            : 'Chart data failed to load or the request timed out.'}
        </span>
        <button type="button" className="chart-link-btn" onClick={remoteDataset.retry}>
          {lang === 'zh' ? '重新加载' : 'Try again'}
        </button>
      </div>
    );
  }

  if (Object.keys(series).length === 0) {
    if (remoteDataset.isLoading) {
      return (
        <div className="chart-loading-shell" role="status" aria-busy="true" aria-label={lang === 'zh' ? '图表数据加载中' : 'Loading chart data'}>
          <div className="chart-loading-toolbar" aria-hidden="true">
            <span className="chart-loading-pill w-24" />
            <span className="chart-loading-pill w-32" />
            <span className="chart-loading-pill w-28" />
          </div>
          <div className="chart-loading-line w-4/5" aria-hidden="true" />
          <div className="chart-loading-panel" style={{ height }} aria-hidden="true" />
        </div>
      );
    }
    return (
      <div className="chart-shell flex min-h-[160px] items-center justify-center text-sm text-[rgb(var(--text-muted))]">
        {lang === 'zh' ? '暂无图表数据' : 'No chart data available'}
      </div>
    );
  }

  const hasActiveSourceSelection = curveState.activeIds.some(
    (id) => Boolean(effectiveSourceSelection[id])
  );
  const hasActivePublicProjection = curveState.activeIds.some(
    (id) => !effectiveSourceSelection[id] && hasPublicProjection(enrichedSeries[id])
  );
  const metricDisplayLabel = effectiveMetric === 'incidence_rates'
    ? formatIncidenceMetricLabel(Array.from(activeGranularities), lang)
    : effectiveMetric === 'historical_index' && effectiveComparisonFrequencyMode === 'seasonal_index'
      ? (lang === 'zh' ? '相对历史同期预期（%）' : 'Observed / historical expected (%)')
      : effectiveMetric === 'cases' && effectiveComparisonFrequencyMode === 'annual_total'
        ? (lang === 'zh' ? '完整自然年报告病例总数' : 'Complete calendar-year reported cases')
      : effectiveMetric === 'deaths' && effectiveComparisonFrequencyMode === 'annual_total'
        ? (lang === 'zh' ? '完整自然年报告死亡总数' : 'Complete calendar-year reported deaths')
      : effectiveMetric === 'cases' && hasActiveSourceSelection
      ? hasActivePublicProjection
        ? (lang === 'zh' ? '来源序列／公开投影期间值' : 'Source-series / public-projection period values')
        : (lang === 'zh' ? '所选来源序列期间值' : 'Selected source-series period values')
      : METRIC_LABELS[effectiveMetric][lang];
  const referencePointCount = lines.reduce(
    (total, line) => total + (line.reference?.expected.filter((value) => value != null).length ?? 0),
    0
  );
  const provisionalTailCount = activeItems.filter(
    (item) => provisionalDisplayScope(item) === 'tail'
  ).length;
  const provisionalSeriesCount = activeItems.filter(
    (item) => provisionalDisplayScope(item) === 'series'
  ).length;
  const primaryMetrics = curveState.availableMetrics.filter((metric) => (
    analysisMode === 'outbreak'
      ? metric === 'cases'
      : analysisMode === 'compare'
        ? !['trend_index', 'weekly_equiv_cases'].includes(metric)
        : !['historical_index', 'trend_index', 'weekly_equiv_cases'].includes(metric)
  ));
  const displayedPrimaryMetrics = primaryMetrics.includes(curveState.metric)
    ? primaryMetrics
    : [...primaryMetrics, curveState.metric];
  const advancedMetrics = curveState.availableMetrics.filter((metric) => (
    ['trend_index', 'weekly_equiv_cases'].includes(metric)
  ));
  const setMode = (nextMode: EpidemicAnalysisMode) => {
    setAnalysisMode(nextMode);
    if (nextMode !== 'compare') setComparisonFrequencyMode('native');
    curveState.setSelectionMode(nextMode === 'compare' ? 'multiple' : 'single');
    if (nextMode === 'outbreak') {
      curveState.setMetric('cases');
    } else if (
      nextMode === 'compare'
      && ['trend_index', 'weekly_equiv_cases'].includes(curveState.metric)
    ) {
      curveState.setMetric(
        curveState.availableMetrics.includes('historical_index') ? 'historical_index' : 'cases'
      );
    }
  };
  const emptyMessage = comparisonBlocked
    ? (effectiveComparisonFrequencyMode === 'annual_total' && !annualCommonWindow
        ? (lang === 'zh'
            ? '所选序列没有共同的完整自然年，不能进行年度总量比较。'
            : 'The selected series have no shared complete calendar year for annual-total comparison.')
        : (lang === 'zh'
            ? '所选序列不满足直接叠加条件；请移除不可比来源或改用单序列监测。'
            : 'The selected series do not meet overlay requirements; remove non-comparable sources or use single-series surveillance.'))
    : outbreakBlocked
      ? (lang === 'zh'
          ? '当前数据不满足按发病时间绘制日／周暴发曲线的条件。'
          : 'The current data do not meet the requirements for a daily/weekly onset-time outbreak curve.')
      : (lang === 'zh' ? '当前指标没有可绘制数据。' : 'No plottable data for the current metric.');
  const statusMessages: string[] = [];
  if (analysisMode === 'compare') {
    if (curveState.activeIds.length < 2) {
      statusMessages.push(lang === 'zh'
        ? '再选择一个对象即可开始比较'
        : 'Select one more item to begin comparison');
    } else if (effectiveComparisonFrequencyMode === 'annual_total' && !annualCommonWindow) {
      statusMessages.push(lang === 'zh'
        ? '没有共同的完整自然年，无法生成年度总量比较'
        : 'No shared complete calendar year is available for annual-total comparison');
    } else if (comparisonAssessment.level === 'blocked') {
      const primaryReason = comparisonAssessment.reasons[0];
      statusMessages.push(primaryReason
        ? `${lang === 'zh' ? '无法直接叠加' : 'Overlay blocked'}: ${comparisonReasonLabel(primaryReason, lang)}`
        : (lang === 'zh' ? '所选序列无法直接叠加' : 'The selected series cannot be overlaid'));
    } else if (comparisonAssessment.level === 'conditional') {
      statusMessages.push(effectiveComparisonFrequencyMode === 'annual_total'
        ? (lang === 'zh'
            ? '报告频率已按完整自然年对齐；来源口径差异仍需谨慎解读'
            : 'Reporting cadence is aligned to complete calendar years; interpret remaining source differences cautiously')
        : facetByGranularity
        ? (lang === 'zh'
            ? '来源或频率不同，已分面显示；请谨慎解读'
            : 'Sources or cadences differ; panels are separated. Interpret cautiously')
        : (lang === 'zh'
            ? '来源定义不同，本结果仅作条件比较'
            : 'Source definitions differ; this is a conditional comparison'));
    }
  }
  if (effectiveComparisonFrequencyMode === 'seasonal_index') {
    statusMessages.push(lang === 'zh'
      ? '按各序列自身历史同期中位数标准化；用于比较异常强度，不用于比较绝对负担'
      : 'Normalized to each series’ historical median for the same period; compare anomaly intensity, not absolute burden');
  }
  if (effectiveComparisonFrequencyMode === 'annual_total') {
    const excludedCount = annualAggregation.excludedYearsBySeries
      .reduce((total, years) => total + years.length, 0);
    statusMessages.push(lang === 'zh'
      ? `仅汇总完整自然年；不插值、不拆分跨期报告${excludedCount > 0 ? `，已排除 ${excludedCount} 个不完整年份` : ''}`
      : `Only complete calendar years are summed; no interpolation or cross-period splitting${excludedCount > 0 ? `; ${excludedCount} incomplete year entries excluded` : ''}`);
  }
  if (effectiveMetric === 'incidence_rates') {
    statusMessages.push(lang === 'zh'
      ? '按病例数 ÷ 同年人口 × 100,000 计算；不作年化'
      : 'Calculated as cases ÷ same-year population × 100,000; not annualized');
  }
  if (provisionalTailCount > 0) {
    statusMessages.push(lang === 'zh'
      ? `${provisionalTailCount} 条序列的阴影尾段为暂定数据，可能因补报而修订`
      : `${provisionalTailCount} series have a shaded provisional tail that may be revised`);
  }
  if (provisionalSeriesCount > 0) {
    statusMessages.push(lang === 'zh'
      ? `${provisionalSeriesCount} 条序列由来源整体标为暂定／可修订（不铺设整图阴影）`
      : `${provisionalSeriesCount} series are source-wide provisional/revisable (no full-chart shading)`);
  }
  if (effectiveMetric === 'historical_index' && !comparisonBlocked && hiddenMetricSeriesCount > 0) {
    statusMessages.push(lang === 'zh'
      ? `${hiddenMetricSeriesCount} 条序列因历史不足未纳入比较`
      : `${hiddenMetricSeriesCount} series are omitted because their history is insufficient`);
  }
  if (sourceOnlySelectionRequired) {
    statusMessages.push(lang === 'zh'
      ? '请先选择一条来源序列以绘制曲线'
      : 'Choose a source series to plot the curve');
  }
  if (outbreakBlocked) {
    statusMessages.push(lang === 'zh'
      ? '当前数据已不满足暴发曲线条件，请返回趋势视图'
      : 'The data no longer meet outbreak-curve requirements; return to trend view');
  }

  const sourceControlPanel = sourceControls.length > 0 ? (
    <div className={`chart-source-controls ${sourceOnlySelectionRequired ? 'chart-source-controls-required' : ''}`}>
      <div className="chart-source-controls-title">
        {lang === 'zh'
          ? `具体来源序列（${sourceControls.length} 个当前对象）`
          : `Specific source series (${sourceControls.length} active items)`}
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {sourceControls.map((control) => {
          const source = control.displaySource;
          return (
            <label key={control.id} className="block min-w-0 text-xs text-[rgb(var(--text-muted))]">
              <span className="mb-1 block truncate font-medium text-[rgb(var(--text-strong))]">
                {lang === 'zh' ? control.item.name_zh : control.item.name_en}
              </span>
              <select
                id={`epidemic-curve-source-${control.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`}
                name={`epidemic-curve-source-${control.id.replace(/[^a-zA-Z0-9_-]/g, '-')}`}
                className="site-control-input w-full rounded-none border px-2 py-1.5 text-xs"
                value={control.selectedCode}
                onChange={(event) => {
                  const nextCode = event.target.value;
                  setSourceSelectionById((current) => {
                    const next = { ...current };
                    if (nextCode) next[control.id] = nextCode;
                    else delete next[control.id];
                    return next;
                  });
                  if (!curveState.availableMetrics.includes(curveState.metric)) {
                    curveState.setMetric('cases');
                  }
                }}
                aria-label={lang === 'zh'
                  ? `${control.item.name_zh}曲线来源序列`
                  : `${control.item.name_en} curve source series`}
              >
                {control.publicProjectionAvailable ? (
                  <option value="">{lang === 'zh' ? '公开投影（默认）' : 'Public projection (default)'}</option>
                ) : (
                  <option value="" disabled>{lang === 'zh' ? '请选择来源序列' : 'Select a source series'}</option>
                )}
                {control.sources.map((candidate) => (
                  <option key={candidate.series_code} value={candidate.series_code}>
                    {candidate.source_label ?? candidate.series_code}
                  </option>
                ))}
              </select>
              {source && (
                <span className="mt-1 block leading-5">
                  {lang === 'zh' ? '指标' : 'Metric'}: {(source.metric_type ?? 'unknown').replaceAll('_', ' ')}
                  {' · '}
                  {lang === 'zh' ? '报告粒度' : 'Grain'}: {formatTemporalGranularity(source.temporal_granularity, lang)}
                  {' · '}
                  {lang === 'zh' ? '报告口径' : 'Basis'}: {(source.reporting_basis ?? 'unknown').replaceAll('_', ' ')}
                  {' · '}
                  {lang === 'zh' ? '时间基准' : 'Time basis'}: {(source.time_basis ?? 'unknown').replaceAll('_', ' ')}
                  {' · '}
                  {lang === 'zh' ? '可比性' : 'Comparability'}: {(source.comparability ?? 'unknown').replaceAll('_', ' ')}
                  {' · '}
                  {lang === 'zh' ? '可用状态' : 'Availability'}: {(source.availability_status ?? 'unknown').replaceAll('_', ' ')}
                </span>
              )}
            </label>
          );
        })}
      </div>
    </div>
  ) : null;

  const toolbar = (
    <>
      <div className="chart-primary-controls">
        <div className="chart-selection-mode" role="group" aria-label={lang === 'zh' ? '查看方式' : 'View mode'}>
          <button
            type="button"
            onClick={() => setMode('monitor')}
            aria-pressed={analysisMode === 'monitor'}
            className={`chart-toggle ${analysisMode === 'monitor' ? 'chart-toggle-active' : ''}`}
          >
            {analysisMode === 'outbreak'
              ? (lang === 'zh' ? '返回趋势' : 'Back to trend')
              : (lang === 'zh' ? '查看趋势' : 'View trend')}
          </button>
          <button
            type="button"
            onClick={() => setMode('compare')}
            aria-pressed={analysisMode === 'compare'}
            className={`chart-toggle ${analysisMode === 'compare' ? 'chart-toggle-active' : ''}`}
          >
            {lang === 'zh' ? '开始比较' : 'Start comparison'}
          </button>
        </div>
        {analysisMode === 'compare' && (
          <div className="chart-comparison-frequency" role="group" aria-label={lang === 'zh' ? '比较对齐方式' : 'Comparison alignment'}>
            <span className="chart-control-label">{lang === 'zh' ? '对齐' : 'Align'}</span>
            <button
              type="button"
              onClick={() => setComparisonFrequencyMode('native')}
              aria-pressed={effectiveComparisonFrequencyMode === 'native'}
              className={`chart-toggle chart-toggle-small ${effectiveComparisonFrequencyMode === 'native' ? 'chart-toggle-active' : ''}`}
              title={lang === 'zh' ? '保留原始报告频率，并按频率分面' : 'Keep native reporting cadence and facet by frequency'}
            >
              {lang === 'zh' ? '原频率' : 'Native'}
            </button>
            <button
              type="button"
              onClick={() => setComparisonFrequencyMode('seasonal_index')}
              disabled={!seasonalComparisonAvailable}
              aria-pressed={effectiveComparisonFrequencyMode === 'seasonal_index'}
              className={`chart-toggle chart-toggle-small ${effectiveComparisonFrequencyMode === 'seasonal_index' ? 'chart-toggle-active' : ''}`}
              title={lang === 'zh' ? '相对各自历史同期预期的异常强度' : 'Anomaly intensity relative to each series’ historical expectation'}
            >
              {lang === 'zh' ? '同期异常' : 'Seasonal'}
            </button>
            <button
              type="button"
              onClick={() => setComparisonFrequencyMode('annual_total')}
              disabled={!annualAggregation.eligible}
              aria-pressed={effectiveComparisonFrequencyMode === 'annual_total'}
              className={`chart-toggle chart-toggle-small ${effectiveComparisonFrequencyMode === 'annual_total' ? 'chart-toggle-active' : ''}`}
              title={lang === 'zh' ? '仅聚合完整自然年的可加报告计数' : 'Sum additive reported counts for complete calendar years only'}
            >
              {lang === 'zh' ? '完整年度' : 'Annual'}
            </button>
          </div>
        )}
        <label className="chart-metric-select">
          <span>{lang === 'zh' ? '指标' : 'Metric'}</span>
          <select
            id="epidemic-curve-metric"
            name="epidemic-curve-metric"
            className="site-control-input rounded-none border px-2 py-1.5 text-xs"
            value={effectiveMetric}
            onChange={(event) => {
              setComparisonFrequencyMode('native');
              curveState.setMetric(event.target.value as typeof curveState.metric);
            }}
          >
            {displayedPrimaryMetrics.map((metric) => (
              <option key={metric} value={metric}>
                {metric === effectiveMetric ? metricDisplayLabel : METRIC_LABELS[metric][lang]}
              </option>
            ))}
          </select>
        </label>
        {plottedDateWindow && (
          <>
            <div className="chart-date-shortcuts" role="group" aria-label={lang === 'zh' ? '时间范围' : 'Time range'}>
              {[
                [6, lang === 'zh' ? '近6月' : '6M'],
                [12, lang === 'zh' ? '近12月' : '12M'],
                [36, lang === 'zh' ? '近3年' : '3Y'],
              ].map(([months, label]) => {
                const nextWindow = trailingDateWindow(plottedDates, months as number);
                const isActive = nextWindow?.startDate === plottedDateWindow.startDate
                  && nextWindow.endDate === plottedDateWindow.endDate;
                return (
                  <button
                    key={months}
                    type="button"
                    onClick={() => nextWindow && curveState.setDateWindow(nextWindow)}
                    aria-pressed={isActive}
                    className={`chart-toggle chart-toggle-small ${isActive ? 'chart-toggle-active' : ''}`}
                  >
                    {label}
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => curveState.setDateWindow({ startDate: plottedDates[0], endDate: plottedDates[plottedDates.length - 1] })}
                aria-pressed={plottedDateWindow.startDate === plottedDates[0] && plottedDateWindow.endDate === plottedDates[plottedDates.length - 1]}
                className={`chart-toggle chart-toggle-small ${plottedDateWindow.startDate === plottedDates[0] && plottedDateWindow.endDate === plottedDates[plottedDates.length - 1] ? 'chart-toggle-active' : ''}`}
              >
                {lang === 'zh' ? '全部' : 'All'}
              </button>
            </div>
            <span className="chart-date-range">
              {analysisMode === 'compare' && curveState.activeIds.length >= 2
                ? (lang === 'zh' ? '共同时间窗 ' : 'Common window ')
                : ''}
              {plottedDateWindow.startDate} → {plottedDateWindow.endDate}
            </span>
          </>
        )}
      </div>
      {sourceOnlySelectionRequired && sourceControlPanel}
      {statusMessages.length > 0 && (
        <div
          className={`chart-status-line ${comparisonAssessment.level !== 'direct' || sourceOnlySelectionRequired || outbreakBlocked ? 'chart-status-line-warning' : ''}`}
          role="status"
        >
          {statusMessages.join(' · ')}
        </div>
      )}
      <details className="chart-advanced-controls">
        <summary>
          {lang === 'zh' ? '高级分析与数据来源' : 'Advanced analysis & data source'}
          {sourceOnlySelectionRequired ? (lang === 'zh' ? '（需要选择）' : ' (selection required)') : ''}
        </summary>
        <div className="chart-advanced-body">
          <div className="chart-toolbar">
            {advancedMetrics.map((metric) => (
              <button
                key={metric}
                type="button"
                onClick={() => {
                  if (analysisMode === 'outbreak') setMode('monitor');
                  curveState.setMetric(metric);
                }}
                className={`chart-toggle ${curveState.metric === metric ? 'chart-toggle-active' : ''}`}
              >
                {METRIC_LABELS[metric][lang]}
              </button>
            ))}
            {outbreakEligibility.eligible && (
              <button
                type="button"
                onClick={() => setMode(analysisMode === 'outbreak' ? 'monitor' : 'outbreak')}
                className={`chart-toggle ${analysisMode === 'outbreak' ? 'chart-toggle-active' : ''}`}
              >
                {analysisMode === 'outbreak'
                  ? (lang === 'zh' ? '返回监测趋势' : 'Return to surveillance')
                  : (lang === 'zh' ? '按发病时间绘制暴发曲线' : 'Plot onset-time outbreak curve')}
              </button>
            )}
          </div>
          {analysisMode === 'monitor' && curveState.metric === 'cases' && referencePointCount > 0 && (
            <p className="chart-advanced-note">
              {lang === 'zh'
                ? `历史参照：${referencePointCount} 个可评估点`
                : `Historical reference: ${referencePointCount} evaluable points`}
            </p>
          )}
          {sourceControls.length > 0 && (
            !sourceOnlySelectionRequired && sourceControlPanel
          )}
        </div>
      </details>
    </>
  );

  const legend = (
    <div className="chart-legend">
      {lines.map((line) => {
        const source = series[line.id];
        return (
          <div className="chart-legend-item" key={line.id}>
            <div className="chart-legend-row">
              <span
                className="chart-legend-swatch"
                style={{ backgroundColor: line.color }}
              />
              <div>
                <div className="chart-legend-name">{line.name}</div>
                <div className="chart-legend-meta">
                  {lang === 'zh' ? '最新值' : 'Latest'} {formatCellValue(findLatestValue(line.values), ['incidence_rates', 'historical_index', 'trend_index'].includes(effectiveMetric) ? 2 : 0)}
                  {' · '}
                  {formatTemporalGranularity(line.granularity, lang)}
                  {line.timeBasis ? <> · {lang === 'zh' ? '时间' : 'Time'}: {line.timeBasis}</> : null}
                  {line.reference ? <> · {lang === 'zh' ? '含历史参照带' : 'historical reference shown'}</> : null}
                  {effectiveMetric === 'cases' || effectiveMetric === 'weekly_equiv_cases' ? (
                    <> · {lang === 'zh' ? '累计病例' : 'Reported total'} {(source.total_cases ?? 0).toLocaleString()}</>
                  ) : effectiveMetric === 'deaths' ? (
                    <> · {lang === 'zh' ? '累计死亡' : 'Reported total'} {(source.total_deaths ?? 0).toLocaleString()}</>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
  const notes = sourceNotes.length > 0 ? (
    <details className="chart-note-details" open={sourceNotes.length <= 3}>
      <summary>
        {lang === 'zh'
          ? `注释信息（${sourceNotes.length} 条当前曲线）`
          : `Data notes (${sourceNotes.length} active series)`}
      </summary>
      <ul className="chart-note-list">
        {sourceNotes.map((note) => (
          <li key={note.id}>
            <span className="chart-note-name">{note.name}</span>
            <span>{note.fragments.join(' · ')}</span>
          </li>
        ))}
      </ul>
    </details>
  ) : null;

  const selectorProps = {
    series,
    eligibleIds: curveState.eligibleIds,
    visibleIds: curveState.visibleIds,
    activeIds: curveState.activeIds,
    activeIdSet: curveState.activeIdSet,
    colorById,
    metric: effectiveMetric,
    entityType,
    lang,
    query: curveState.query,
    selectionMode: curveState.selectionMode,
    onQueryChange: curveState.setQuery,
    onToggle: curveState.toggleSelection,
    onReset: curveState.resetSelection,
  };
  const compactSelector = (
    <CurveEntitySelector
      {...selectorProps}
      density="compact"
    />
  );
  const fullSelector = (
    <CurveEntitySelector
      {...selectorProps}
      density="full"
    />
  );

  const renderTable = () => {
    const tableRows = Array.from(new Set(lines.flatMap((line) => line.dates))).sort().map((date) => ({
      date,
      values: lines.map((line) => {
        const pointIndex = line.dates.indexOf(date);
        return pointIndex >= 0 ? line.values[pointIndex] : null;
      }),
    }));
    return (
      <>
        <div className="data-preview-meta">
          {['historical_index', 'trend_index'].includes(effectiveMetric)
            ? (lang === 'zh'
                ? `${metricDisplayLabel} · ${tableRows.length} 行`
                : `${metricDisplayLabel} · ${tableRows.length} rows`)
            : (lang === 'zh'
                ? `${metricDisplayLabel} · ${tableRows.length} 行`
                : `${metricDisplayLabel} · ${tableRows.length} rows`)}
        </div>
        <table className="data-preview-table">
          <thead>
            <tr>
              <th className="is-sticky">{lang === 'zh' ? '日期' : 'Date'}</th>
              {lines.map((line) => <th key={line.id}>{line.name}</th>)}
            </tr>
          </thead>
          <tbody>
            {tableRows.map((row) => (
              <tr key={row.date}>
                <td className="is-sticky">{row.date}</td>
                {row.values.map((value, index) => (
                  <td key={`${row.date}-${lines[index].id}`}>
                    {formatCellValue(value, ['incidence_rates', 'historical_index', 'trend_index'].includes(effectiveMetric) ? 2 : 0)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </>
    );
  };

  return (
    <ChartFrame
      lang={lang}
      toolbar={toolbar}
      chart={({ isFullscreen }) => (
        <EpidemicCurvePlot
          lines={lines}
          activeDates={plottedDates}
          dateWindow={plottedDateWindow}
          onDateWindowChange={curveState.setDateWindow}
          metric={effectiveMetric}
          metricLabel={metricDisplayLabel}
          lang={lang}
          colors={colors}
          title={title}
          height={isFullscreen ? '100%' : chartHeight}
          facetByGranularity={facetByGranularity}
          analysisMode={analysisMode}
          emptyMessage={emptyMessage}
        />
      )}
      table={renderTable}
      legend={legend}
      notes={notes}
      sidebar={compactSelector}
      fullscreenSidebar={fullSelector}
      stageHeight={chartHeight}
      sourceMeta={sourceMeta}
    />
  );
}
