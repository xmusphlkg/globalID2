import { useMemo } from 'react';
import ChartFrame from './ChartFrame';
import MonthlyBarPlot from './MonthlyBarPlot';
import YearSelector from './YearSelector';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { useChartLanguage, useChartTheme } from './chartPreferences';
import { useMonthlyBarState } from './useMonthlyBarState';
import {
  MONTH_NAMES,
  type MonthlyData,
  type MonthlyMetric,
} from './monthlyBarModel';

interface Props {
  data: MonthlyData;
  title?: string;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
}

function formatValue(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString();
}

export default function MonthlyBar({
  data,
  title,
  height = 380,
  sourceMeta = null,
}: Props) {
  const theme = useChartTheme();
  const lang = useChartLanguage();
  const state = useMonthlyBarState(data);

  const colors = useMemo(() => (
    theme === 'light'
      ? {
          font: '#556070',
          line: '#c9c2b8',
          grid: '#d9d2c7',
          hoverBg: '#fffdfa',
          hoverBorder: '#c9c2b8',
          hoverFont: '#162232',
          title: '#162232',
        }
      : {
          font: '#a4b1c1',
          line: '#51667f',
          grid: '#344a64',
          hoverBg: '#17283a',
          hoverBorder: '#51667f',
          hoverFont: '#f3f6fb',
          title: '#f3f6fb',
        }
  ), [theme]);

  const metricLabel = state.metric === 'cases'
    ? (lang === 'zh' ? '病例数' : 'Cases')
    : (lang === 'zh' ? '死亡数' : 'Deaths');
  const peakValue = Math.max(
    0,
    ...state.activeYearSummaries.flatMap((summary) => summary.values)
  );

  const toolbar = (
    <>
      <div className="chart-toolbar">
        {(['cases', 'deaths'] as MonthlyMetric[])
          .filter((metric) => metric !== 'deaths' || state.hasDeathsMetric)
          .map((metric) => (
            <button
              key={metric}
              type="button"
              onClick={() => state.setMetric(metric)}
              className={`chart-toggle ${state.metric === metric ? 'chart-toggle-active' : ''}`}
            >
              {metric === 'cases'
                ? (lang === 'zh' ? '病例数' : 'Cases')
                : (lang === 'zh' ? '死亡数' : 'Deaths')}
            </button>
          ))}
      </div>
      <span className="chart-chip">
        {lang === 'zh'
          ? `显示 ${state.activeYearSummaries.length} / ${state.allYears.length} 个年份`
          : `${state.activeYearSummaries.length}/${state.allYears.length} years`}
      </span>
      <span className="chart-chip">
        {lang === 'zh'
          ? `月峰值 ${peakValue.toLocaleString()}`
          : `Monthly peak ${peakValue.toLocaleString()}`}
      </span>
    </>
  );

  const note = lang === 'zh'
    ? '按自然月比较不同年份的总量，用于识别季节性和异常峰值。可在右侧快速切换年份，全屏模式提供完整筛选信息。'
    : 'Compare calendar-month totals across years to reveal seasonality and unusual peaks. Use the year selector on the right; full-screen adds detailed filtering.';

  const legend = (
    <div className="chart-legend">
      {state.activeYearSummaries.map((summary) => (
        <div className="chart-legend-item" key={summary.year}>
          <div className="chart-legend-row">
            <span
              className="chart-legend-swatch"
              style={{ backgroundColor: summary.color }}
            />
            <div>
              <div className="chart-legend-name">{summary.year}</div>
              <div className="chart-legend-meta">
                {lang === 'zh' ? '年度合计' : 'Year total'} {formatValue(summary.total)}
                {' · '}
                {lang === 'zh' ? '峰值' : 'Peak'} {summary.peakMonth}
                {summary.peakMonth !== '—' ? ` (${formatValue(summary.peakValue)})` : ''}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );

  const selectorProps = {
    lang,
    allYears: state.allYears,
    recentYears: state.recentYears,
    selectedYearSet: state.selectedYearSet,
    visibleYearSummaries: state.visibleYearSummaries,
    query: state.query,
    onQueryChange: state.setQuery,
    onToggleYear: state.toggleYear,
    onSelectRecent: state.selectRecentYears,
    onSelectAll: state.selectAllYears,
    onSelectVisible: state.selectVisibleYears,
    isShowingRecentYears: state.isShowingRecentYears,
    isShowingAllYears: state.isShowingAllYears,
  };
  const compactSelector = <YearSelector {...selectorProps} density="compact" />;
  const fullSelector = <YearSelector {...selectorProps} density="full" />;

  const renderTable = () => (
    <>
      <div className="data-preview-meta">
        {lang === 'zh'
          ? `月度数据预览。行表示月份，列表示已选年份，单元格显示${metricLabel}。`
          : `Monthly data preview. Rows show months, columns show selected years, and cells report ${metricLabel}.`}
      </div>
      <table className="data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">{lang === 'zh' ? '月份' : 'Month'}</th>
            {state.activeYearSummaries.map((summary) => (
              <th key={summary.year}>{summary.year}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MONTH_NAMES.map((monthName, monthIndex) => (
            <tr key={monthName}>
              <td className="is-sticky">{monthName}</td>
              {state.activeYearSummaries.map((summary) => (
                <td key={`${summary.year}-${monthName}`}>
                  {formatValue(summary.values[monthIndex])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );

  return (
    <ChartFrame
      lang={lang}
      toolbar={toolbar}
      note={note}
      chart={({ isFullscreen }) => (
        <MonthlyBarPlot
          summaries={state.activeYearSummaries}
          metricLabel={metricLabel}
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
