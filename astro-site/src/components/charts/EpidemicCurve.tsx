import { useMemo } from 'react';
import ChartFrame from './ChartFrame';
import CurveEntitySelector from './CurveEntitySelector';
import EpidemicCurvePlot from './EpidemicCurvePlot';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { useChartLanguage, useChartTheme } from './chartPreferences';
import { useCountryDataset } from './useCountryDataset';
import { useEpidemicCurveState } from './useEpidemicCurveState';
import {
  ANIMATION_POINT_LIMIT,
  METRIC_LABELS,
  buildTableRows,
  findLatestValue,
  getMetricValues,
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

export default function EpidemicCurve({
  series: initialSeries,
  dataUrl,
  title,
  topN = 10,
  entityIds,
  entityType = 'disease',
  height = 420,
  sourceMeta = null,
}: Props) {
  const hasInitialSeries = Boolean(initialSeries && Object.keys(initialSeries).length > 0);
  const remoteDataset = useCountryDataset(dataUrl, !hasInitialSeries);
  const series = hasInitialSeries
    ? (initialSeries as Record<string, CurveSeries>)
    : (remoteDataset.data?.disease_series ?? EMPTY_SERIES);
  const theme = useChartTheme();
  const lang = useChartLanguage();
  const curveState = useEpidemicCurveState({
    series,
    entityIds,
    topN,
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
      return {
        id,
        name: lang === 'zh' ? item.name_zh : item.name_en,
        color: SERIES_COLORS[index % SERIES_COLORS.length],
        dates: item.dates,
        values: getMetricValues(item, curveState.metric),
      };
    })
  ), [curveState.activeIds, curveState.metric, lang, series]);
  const colorById = useMemo(
    () => new Map(lines.map((line) => [line.id, line.color])),
    [lines]
  );
  const activePointCount = useMemo(
    () => lines.reduce((total, line) => total + line.values.length, 0),
    [lines]
  );
  const incidenceFallbackPoints = useMemo(() => (
    curveState.activeIds.reduce((total, id) => (
      total + (series[id]?.incidence_sources ?? []).filter((source) => source === 'original_db').length
    ), 0)
  ), [curveState.activeIds, series]);

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
            {METRIC_LABELS[metric][lang]}
          </button>
        ))}
      </div>
      {curveState.dateWindow && (
        <span className="chart-chip">
          {curveState.dateWindow.startDate} → {curveState.dateWindow.endDate}
        </span>
      )}
    </>
  );

  const entityLabel = entityType === 'country'
    ? { zh: '国家', en: 'country' }
    : { zh: '疾病', en: 'disease' };
  const note = (
    <>
      {curveState.metric === 'weekly_equiv_cases'
        ? (lang === 'zh'
            ? '默认指标为周等价病例数，用于减弱不同报告频率导致的比较偏差。'
            : 'Default metric uses weekly-equivalent cases to reduce comparability bias from differing reporting frequencies.')
        : curveState.metric === 'incidence_rates'
          ? (lang === 'zh'
              ? '发病率按每 10 万人口标准化，适合进行跨国家或跨时期强度比较。'
              : 'Incidence rate is standardised per 100k population for cross-country and cross-period comparisons.')
          : (lang === 'zh'
              ? '当前视图显示原始报告值。'
              : 'This view shows raw reported values.')}
      {curveState.activeIds.length > curveState.defaultIds.length && (
        <span>
          {' '}
          {lang === 'zh'
            ? `当前已选择 ${curveState.activeIds.length} 条${entityLabel.zh}序列；可在右侧筛选器中收窄范围。`
            : `You currently have ${curveState.activeIds.length} ${entityLabel.en} series selected; narrow the selection in the sidebar if needed.`}
        </span>
      )}
      {activePointCount > ANIMATION_POINT_LIMIT && (
        <span>
          {' '}
          {lang === 'zh'
            ? '长时间序列已启用保峰抽样并关闭动画，以保持交互流畅；表格仍保留完整数据。'
            : 'Long series use peak-preserving sampling without animation for responsive interaction; the table keeps the full data.'}
        </span>
      )}
      {curveState.metric === 'incidence_rates' && incidenceFallbackPoints > 0 && (
        <span>
          {' '}
          {lang === 'zh'
            ? `包含 ${incidenceFallbackPoints} 个回退到原始数据库发病率的时间点。`
            : `Includes ${incidenceFallbackPoints} fallback points that use the original database incidence rate.`}
        </span>
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
            ? `原始数据预览，共 ${tableRows.length} 行。当前表格显示 ${METRIC_LABELS[curveState.metric][lang]}。`
            : `Raw data preview with ${tableRows.length} rows. The current table shows ${METRIC_LABELS[curveState.metric][lang]}.`}
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
      note={note}
      chart={({ isFullscreen }) => (
        <EpidemicCurvePlot
          lines={lines}
          activeDates={curveState.activeDates}
          dateWindow={curveState.dateWindow}
          onDateWindowChange={curveState.setDateWindow}
          metric={curveState.metric}
          lang={lang}
          colors={colors}
          title={title}
          height={isFullscreen ? '100%' : height}
        />
      )}
      table={renderTable}
      legend={legend}
      sidebar={compactSelector}
      fullscreenSidebar={fullSelector}
      sourceMeta={sourceMeta}
    />
  );
}
