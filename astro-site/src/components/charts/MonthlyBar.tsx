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
  initialLanguage?: 'en' | 'zh' | 'fr';
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
  initialLanguage = 'en',
}: Props) {
  const theme = useChartTheme();
  const lang = useChartLanguage(initialLanguage);
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
    ...state.activeYearSummaries.flatMap((summary) => summary.values.filter((value): value is number => value !== null))
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
      {data.coverage && (
        <span className="chart-chip" role="note">
          {lang === 'zh'
            ? `国家级已报告部分合计；覆盖分母为数据导出中的 ${data.coverage.jurisdiction_universe_size} 个国家/地区，具体报告数见悬浮提示。`
            : lang === 'fr'
              ? `Sous-totaux nationaux déclarés ; couverture mesurée sur ${data.coverage.jurisdiction_universe_size} juridictions nationales dans cet export. Voir les infobulles pour le détail mensuel.`
              : `National reported subtotals; coverage is measured against ${data.coverage.jurisdiction_universe_size} national jurisdictions in this export. See tooltips for monthly coverage.`}
        </span>
      )}
      {!!data.coverage?.excluded_weekly_jurisdictions && (
        <span className="chart-chip" role="note">
          {lang === 'zh'
            ? `${data.coverage.excluded_weekly_jurisdictions} 条国家级周序列未计入月度合计（周期间可能跨月）。`
            : lang === 'fr'
              ? `${data.coverage.excluded_weekly_jurisdictions} séries hebdomadaires nationales exclues du sous-total mensuel (semaines pouvant chevaucher deux mois).`
              : `${data.coverage.excluded_weekly_jurisdictions} national weekly series excluded from monthly subtotals because weeks may cross months.`}
        </span>
      )}
      {!!data.coverage?.excluded_unknown_jurisdictions && (
        <span className="chart-chip" role="note">
          {lang === 'zh'
            ? `${data.coverage.excluded_unknown_jurisdictions} 条粒度不明的国家级序列未计入月度合计。`
            : lang === 'fr'
              ? `${data.coverage.excluded_unknown_jurisdictions} séries nationales de périodicité inconnue exclues du sous-total mensuel.`
              : `${data.coverage.excluded_unknown_jurisdictions} national series of unknown cadence excluded from monthly subtotals.`}
        </span>
      )}
      {!!data.coverage?.inferred_monthly_jurisdictions && (
        <span className="chart-chip" role="note">
          {lang === 'zh'
            ? `${data.coverage.inferred_monthly_jurisdictions} 条旧序列仅按月初日期推断为月度，来源未声明粒度。`
            : lang === 'fr'
              ? `${data.coverage.inferred_monthly_jurisdictions} séries historiques interprétées comme mensuelles d'après leurs dates ; périodicité non déclarée.`
              : `${data.coverage.inferred_monthly_jurisdictions} legacy series treated as monthly from month-start dates; source cadence is undeclared.`}
        </span>
      )}
      {!!(data.coverage?.cases_incompatible_months?.some(Boolean)
        || data.coverage?.deaths_incompatible_months?.some(Boolean)) && (
        <span className="chart-chip" role="note">
          {lang === 'zh'
            ? '来源定义不兼容的月份不计算多国合计；报告数仍见悬浮提示。'
            : lang === 'fr'
              ? 'Les mois aux définitions incompatibles n’ont pas de total multinational ; le nombre de déclarants reste indiqué.'
              : 'Months with incompatible source definitions have no multi-country subtotal; reporting counts remain visible.'}
        </span>
      )}
      <span className="chart-chip">
        {lang === 'zh'
          ? `月峰值 ${peakValue.toLocaleString()}`
          : `Monthly peak ${peakValue.toLocaleString()}`}
      </span>
    </>
  );

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
                {summary.total == null
                  ? (lang === 'zh' ? '年度总量不可完整计算' : 'Annual total incomplete')
                  : <>{lang === 'zh' ? '完整年度合计' : 'Complete-year total'} {formatValue(summary.total)}</>}
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
          ? `${metricLabel} · ${state.activeYearSummaries.length} 个年份`
          : `${metricLabel} · ${state.activeYearSummaries.length} years`}
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
      chart={({ isFullscreen }) => (
        <MonthlyBarPlot
          summaries={state.activeYearSummaries}
          metricLabel={metricLabel}
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
      stageHeight={height}
      sourceMeta={sourceMeta}
      pngFilename="gids-monthly-bars.png"
    />
  );
}
