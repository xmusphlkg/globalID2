import { useMemo, useState } from 'react';
import ChartFrame from './ChartFrame';
import CurveEntitySelector from './CurveEntitySelector';
import EpidemicCurvePlot from './EpidemicCurvePlot';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { useChartLanguage, useChartTheme } from './chartPreferences';
import { useCountryDataset, useCountrySourceSeries } from './useCountryDataset';
import { useEpidemicCurveState } from './useEpidemicCurveState';
import {
  METRIC_LABELS,
  buildTableRows,
  findLatestValue,
  formatTemporalGranularity,
  getMetricValues,
  getSelectableSourceSeries,
  getSelectedSourceSeries,
  getSeriesGranularity,
  hasMixedSourceGranularities,
  hasPublicProjection,
  selectSourceSeries,
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
}: Props) {
  const hasInitialSeries = Boolean(initialSeries && Object.keys(initialSeries).length > 0);
  const remoteDataset = useCountryDataset(dataUrl, !hasInitialSeries);
  const baseSeries = hasInitialSeries
    ? (initialSeries as Record<string, CurveSeries>)
    : (remoteDataset.data?.disease_series ?? EMPTY_SERIES);
  const sourceSeries = useCountrySourceSeries(sourceSeriesUrl, !hasInitialSeries);
  const [sourceSelectionById, setSourceSelectionById] = useState<Record<string, string>>({});
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
  const lang = useChartLanguage();
  const curveState = useEpidemicCurveState({
    series,
    entityIds,
    topN,
    caseOnlyEntityIds,
  });

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
          title: '#f3f6fb',
        }
  ), [theme]);

  const lines = useMemo(() => (
    curveState.activeIds.map((id, index) => {
      const item = series[id];
      const selectedSources = getSelectedSourceSeries(item);
      const primarySource = selectedSources[0];
      return {
        id,
        name: lang === 'zh' ? item.name_zh : item.name_en,
        color: SERIES_COLORS[index % SERIES_COLORS.length],
        dates: item.dates,
        values: getMetricValues(item, curveState.metric),
        granularity: getSeriesGranularity(item),
        reportingBasis: primarySource?.reporting_basis,
        sourceLabel: primarySource?.source_label,
      };
    })
  ), [curveState.activeIds, curveState.metric, lang, series]);
  const sourceNotes = useMemo(() => (
    curveState.activeIds.flatMap((id) => {
      const item = series[id];
      const selectedSources = getSelectedSourceSeries(item);
      if (selectedSources.length === 0) return [];

      const labels = uniqueValues(
        selectedSources.map((source) => source.source_label ?? source.series_code)
      );
      const granularities = uniqueValues(
        selectedSources.map((source) => formatTemporalGranularity(source.temporal_granularity, lang))
      );
      const bases = uniqueValues(selectedSources.map((source) => source.reporting_basis));
      const definitions = uniqueValues(selectedSources.map((source) => source.case_definition));
      const availability = uniqueValues(selectedSources.map((source) => source.availability_status));
      const policies = uniqueValues(selectedSources.map((source) => source.aggregation_policy));

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
        availability.length > 0
          ? `${lang === 'zh' ? '可用状态' : 'Availability'}: ${availability.join(' / ')}`
          : null,
        policies.length > 0
          ? `${lang === 'zh' ? '聚合策略' : 'Aggregation'}: ${policies.join(' / ')}`
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
  const colorById = useMemo(
    () => new Map(lines.map((line) => [line.id, line.color])),
    [lines]
  );
  const activeGranularities = useMemo(
    () => new Set(lines.map((line) => line.granularity).filter((value) => value !== 'unknown')),
    [lines]
  );
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
  const unresolvedMixedGrain = curveState.activeIds.some((id) => (
    !effectiveSourceSelection[id] && hasMixedSourceGranularities(enrichedSeries[id])
  ));
  const sourceOnlySelectionRequired = sourceControls.some((control) => (
    !control.publicProjectionAvailable && !control.selectedCode
  ));
  if (remoteDataset.loadError && Object.keys(series).length === 0) {
    return (
      <div className="chart-shell flex min-h-[160px] flex-col items-center justify-center gap-3 text-slate-500 text-sm" role="alert">
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
      <div className="chart-shell flex min-h-[160px] items-center justify-center text-sm text-slate-500">
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
  const metricDisplayLabel = curveState.metric === 'cases' && hasActiveSourceSelection
    ? hasActivePublicProjection
      ? (lang === 'zh' ? '来源序列／公开投影期间值' : 'Source-series / public-projection period values')
      : (lang === 'zh' ? '所选来源序列期间值' : 'Selected source-series period values')
    : METRIC_LABELS[curveState.metric][lang];

  const toolbar = (
    <>
      <div className="chart-toolbar">
        {curveState.availableMetrics.map((metric) => (
          <button
            key={metric}
            type="button"
            onClick={() => curveState.setMetric(metric)}
            className={`chart-toggle ${curveState.metric === metric ? 'chart-toggle-active' : ''}`}
          >
            {metric === curveState.metric ? metricDisplayLabel : METRIC_LABELS[metric][lang]}
          </button>
        ))}
      </div>
      {curveState.dateWindow && (
        <span className="chart-chip">
          {curveState.dateWindow.startDate} → {curveState.dateWindow.endDate}
        </span>
      )}
      {(activeGranularities.size > 1 || unresolvedMixedGrain) && (
        <span className="chart-chip" role="status">
          {lang === 'zh'
            ? '混合来源粒度：公开投影按需选择代表序列；期间总量不合并、不归一化且不可直接比较'
            : 'Mixed source grain: the public projection selects a representative where needed; period totals are not combined, normalized, or directly comparable'}
        </span>
      )}
      {sourceOnlySelectionRequired && (
        <span className="chart-chip" role="status">
          {lang === 'zh'
            ? '该疾病没有公开投影；请选择一条来源序列后再绘图'
            : 'This disease has no public projection; select a source series to plot it'}
        </span>
      )}
      {sourceControls.length > 0 && (
        <details className="w-full border-t border-[rgb(var(--border))/0.68] pt-2">
          <summary className="cursor-pointer text-xs font-semibold text-[rgb(var(--text-strong))]">
            {lang === 'zh'
              ? `选择具体来源序列（${sourceControls.length} 个当前疾病）`
              : `Choose source series (${sourceControls.length} active diseases)`}
          </summary>
          <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {sourceControls.map((control) => {
              const source = control.displaySource;
              return (
                <label key={control.id} className="block min-w-0 text-xs text-[rgb(var(--text-muted))]">
                  <span className="mb-1 block truncate font-medium text-[rgb(var(--text-strong))]">
                    {lang === 'zh' ? control.item.name_zh : control.item.name_en}
                  </span>
                  <select
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
                      curveState.setMetric('cases');
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
                      {lang === 'zh' ? '可用状态' : 'Availability'}: {(source.availability_status ?? 'unknown').replaceAll('_', ' ')}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        </details>
      )}
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
                  {lang === 'zh' ? '最新值' : 'Latest'} {formatCellValue(findLatestValue(line.values), curveState.metric === 'incidence_rates' ? 2 : 0)}
                  {' · '}
                  {lang === 'zh' ? '累计' : 'Total'} {(source.total_cases ?? 0).toLocaleString()}
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
    metric: curveState.metric,
    entityType,
    lang,
    query: curveState.query,
    onQueryChange: curveState.setQuery,
    onToggle: curveState.toggleSelection,
    onReset: curveState.resetSelection,
    onSelectVisible: curveState.selectVisible,
    defaultCount: curveState.defaultIds.length,
    selectionLimitHit: curveState.selectionLimitHit,
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
    const tableRows = buildTableRows(series, curveState.activeIds, curveState.metric);
    return (
      <>
        <div className="data-preview-meta">
          {lang === 'zh'
            ? `原始数据预览，共 ${tableRows.length} 行。当前表格显示${metricDisplayLabel}。`
            : `Raw data preview with ${tableRows.length} rows. The current table shows ${metricDisplayLabel}.`}
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
                    {formatCellValue(value, curveState.metric === 'incidence_rates' ? 2 : 0)}
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
          activeDates={curveState.activeDates}
          dateWindow={curveState.dateWindow}
          onDateWindowChange={curveState.setDateWindow}
          metric={curveState.metric}
          metricLabel={metricDisplayLabel}
          lang={lang}
          colors={colors}
          title={title}
          height={isFullscreen ? '100%' : height}
        />
      )}
      table={renderTable}
      legend={legend}
      notes={notes}
      sidebar={compactSelector}
      fullscreenSidebar={fullSelector}
      stageHeight={height}
      sourceMeta={sourceMeta}
    />
  );
}
