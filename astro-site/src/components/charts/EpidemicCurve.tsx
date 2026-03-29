// src/components/charts/EpidemicCurve.tsx
// OWID-style time-series chart with table preview and external legend.

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import type { CSSProperties } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';
import ChartFrame from './ChartFrame';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadCountryDataset, type CountryDatasetSeriesEntry } from './countryDataset';

type DiseaseSeries = CountryDatasetSeriesEntry;

interface Props {
  series?: Record<string, DiseaseSeries>;
  dataUrl?: string;
  title?: string;
  topN?: number;
  diseasIds?: string[];
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
}

type Metric = 'weekly_equiv_cases' | 'cases' | 'deaths' | 'incidence_rates';

const METRIC_LABELS: Record<Metric, { en: string; zh: string }> = {
  weekly_equiv_cases: { en: 'Weekly Equivalent Cases', zh: '周等价病例数' },
  cases: { en: 'Cases', zh: '病例数' },
  deaths: { en: 'Deaths', zh: '死亡数' },
  incidence_rates: { en: 'Incidence rate (per 100k)', zh: '发病率（每10万）' },
};

const SERIES_COLORS = ['#0072b2', '#d55e00', '#009e73', '#cc79a7', '#e69f00', '#56b4e9', '#7f3c8d', '#666666', '#bc5090', '#2f4b7c'];

function formatCellValue(value: number | null | undefined, digits = 0) {
  if (value == null || Number.isNaN(value)) return '—';
  return digits > 0 ? value.toFixed(digits) : value.toLocaleString();
}

export default function EpidemicCurve({ series: initialSeries, dataUrl, title, topN = 10, diseasIds, height = 420, sourceMeta = null }: Props) {
  const [series, setSeries] = useState<Record<string, DiseaseSeries>>(initialSeries ?? {});
  const [loadError, setLoadError] = useState(false);
  const [metric, setMetric] = useState<Metric>('weekly_equiv_cases');
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [sidebarQuery, setSidebarQuery] = useState('');
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const updateTheme = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (initialSeries && Object.keys(initialSeries).length > 0) {
      setSeries(initialSeries);
      setLoadError(false);
      return;
    }
    if (!dataUrl) return;

    let cancelled = false;
    loadCountryDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setSeries(dataset.disease_series ?? {});
        setLoadError(false);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
      });

    return () => {
      cancelled = true;
    };
  }, [dataUrl, initialSeries]);

  const chartColors = useMemo(() => (
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
        }
  ), [theme]);

  const eligibleIds = useMemo(() => {
    let ids = diseasIds ?? Object.keys(series);
    ids = ids
      .filter(id => id in series && series[id]?.category !== 'Summary')
      .sort((a, b) => (series[b]?.total_cases ?? 0) - (series[a]?.total_cases ?? 0))
    return ids;
  }, [series, diseasIds]);
  const defaultIds = useMemo(() => eligibleIds.slice(0, topN), [eligibleIds, topN]);

  useEffect(() => {
    setSelectedIds((current) => {
      const filtered = current.filter((id) => eligibleIds.includes(id));
      if (filtered.length > 0) {
        return filtered;
      }
      return defaultIds;
    });
  }, [defaultIds, eligibleIds]);

  const activeIds = useMemo(() => {
    const sourceIds = selectedIds.length > 0 ? selectedIds : defaultIds;
    return [...sourceIds]
      .filter((id) => eligibleIds.includes(id))
      .sort((a, b) => eligibleIds.indexOf(a) - eligibleIds.indexOf(b));
  }, [defaultIds, eligibleIds, selectedIds]);

  const toggleDiseaseSelection = useCallback((diseaseId: string) => {
    setSelectedIds((current) => {
      if (current.includes(diseaseId)) {
        if (current.length === 1) return current;
        return current.filter((id) => id !== diseaseId);
      }
      return [...current, diseaseId].sort((a, b) => eligibleIds.indexOf(a) - eligibleIds.indexOf(b));
    });
  }, [eligibleIds]);

  const resetToTopDiseases = useCallback(() => {
    setSelectedIds(defaultIds);
  }, [defaultIds]);

  const sidebarDiseaseIds = useMemo(() => {
    const query = sidebarQuery.trim().toLowerCase();
    if (!query) return eligibleIds;
    return eligibleIds.filter((id) => {
      const item = series[id];
      return item.name_en.toLowerCase().includes(query) || item.name_zh.toLowerCase().includes(query);
    });
  }, [eligibleIds, series, sidebarQuery]);

  const totalCasesRange = useMemo(() => {
    if (eligibleIds.length === 0) return { min: 0, max: 0 };

    return eligibleIds.reduce(
      (acc, id) => {
        const total = series[id]?.total_cases ?? 0;
        return {
          min: Math.min(acc.min, total),
          max: Math.max(acc.max, total),
        };
      },
      { min: Number.POSITIVE_INFINITY, max: Number.NEGATIVE_INFINITY }
    );
  }, [eligibleIds, series]);

  const selectVisibleDiseases = useCallback(() => {
    if (sidebarDiseaseIds.length === 0) return;
    setSelectedIds(sidebarDiseaseIds);
  }, [sidebarDiseaseIds]);

  const hasDeathsMetric = useMemo(
    () => activeIds.some((id) => (series[id]?.deaths ?? []).some((value) => (value ?? 0) > 0)),
    [activeIds, series]
  );
  const availableMetrics = useMemo(() => {
    const candidates: Metric[] = ['weekly_equiv_cases', 'cases'];
    if (hasDeathsMetric) candidates.push('deaths');
    candidates.push('incidence_rates');
    return candidates;
  }, [hasDeathsMetric]);

  useEffect(() => {
    if (!availableMetrics.includes(metric)) {
      setMetric('weekly_equiv_cases');
    }
  }, [availableMetrics, metric]);

  const seriesMeta = useMemo(() => activeIds.map((id, index) => {
    const item = series[id];
    const values = metric === 'incidence_rates'
      ? item.incidence_rates
      : metric === 'weekly_equiv_cases'
        ? item.weekly_equiv_cases
        : item[metric];
    const latestValue = [...values].reverse().find((value) => value != null) ?? null;
    return {
      id,
      name: lang === 'zh' ? item.name_zh : item.name_en,
      color: SERIES_COLORS[index % SERIES_COLORS.length],
      values,
      latestValue,
      totalCases: item.total_cases,
    };
  }), [activeIds, series, metric, lang]);
  const seriesColorMap = useMemo(
    () => new Map(seriesMeta.map((item) => [item.id, item.color])),
    [seriesMeta]
  );

  const dateRange = useMemo(() => {
    const dates = activeIds.flatMap(id => series[id]?.dates ?? []);
    if (dates.length === 0) return null;
    return { start: dates[0], end: dates[dates.length - 1] };
  }, [activeIds, series]);

  const sortedDates = useMemo(() => (
    Array.from(new Set(activeIds.flatMap((id) => series[id]?.dates ?? []))).sort()
  ), [activeIds, series]);

  const tableRows = useMemo(() => {
    const lookups = Object.fromEntries(activeIds.map((id) => {
      const item = series[id];
      const values = metric === 'incidence_rates'
        ? item.incidence_rates
        : metric === 'weekly_equiv_cases'
          ? item.weekly_equiv_cases
          : item[metric];
      return [
        id,
        new Map(item.dates.map((date, index) => [date, values[index] ?? null])),
      ];
    }));

    return sortedDates.map((date) => ({
      date,
      values: activeIds.map((id) => (lookups[id] as Map<string, number | null>).get(date) ?? null),
    }));
  }, [activeIds, metric, series, sortedDates]);

  const incidenceFallbackPoints = useMemo(() => {
    let count = 0;
    for (const id of activeIds) {
      for (const sourceTag of series[id]?.incidence_sources ?? []) {
        if (sourceTag === 'original_db') count += 1;
      }
    }
    return count;
  }, [activeIds, series]);

  const option = useMemo(() => ({
    animationDuration: 240,
    backgroundColor: 'transparent',
    title: title
      ? { text: title, textStyle: { color: theme === 'light' ? '#162232' : '#f3f6fb', fontSize: 15, fontWeight: 600 }, left: 0, top: 0 }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' as const, lineStyle: { color: chartColors.line, type: 'dashed' as const } },
      backgroundColor: chartColors.hoverBg,
      borderColor: chartColors.hoverBorder,
      borderWidth: 1,
      textStyle: { color: chartColors.hoverFont, fontSize: 12 },
      formatter: (params: any[]) => {
        const date = params[0]?.axisValueLabel ?? params[0]?.axisValue ?? '';
        return [
          `<b>${date}</b>`,
          ...params.map((param) => `${param.marker}${param.seriesName}: <b>${formatCellValue(param.value?.[1] ?? param.value, metric === 'incidence_rates' ? 2 : 0)}</b>`),
        ].join('<br/>');
      },
    },
    grid: { left: 64, right: 18, top: title ? 38 : 16, bottom: 54 },
    xAxis: {
      type: 'time' as const,
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.line } },
      splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' as const } },
    },
    yAxis: {
      type: 'value' as const,
      name: METRIC_LABELS[metric][lang],
      nameTextStyle: { color: chartColors.font, fontSize: 11, padding: [0, 0, 8, 0] },
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.line } },
      splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' as const } },
      min: 0,
    },
    dataZoom: [
      { type: 'inside' as const, filterMode: 'none' as const },
      {
        type: 'slider' as const,
        bottom: 8,
        height: 16,
        backgroundColor: chartColors.sliderBg,
        borderColor: chartColors.line,
        textStyle: { color: chartColors.font, fontSize: 10 },
        fillerColor: chartColors.fillerColor,
      },
    ],
    series: seriesMeta.map((item, index) => {
      const sourceSeries = series[item.id];
      return {
        name: item.name,
        type: 'line' as const,
        showSymbol: false,
        smooth: false,
        lineStyle: {
          color: item.color,
          width: 2.4,
          type: 'solid' as const,
        },
        itemStyle: { color: item.color },
        emphasis: { focus: 'series' as const },
        z: 10 + index,
        data: sourceSeries.dates.map((date, valueIndex) => [date, item.values[valueIndex] ?? null]),
      };
    }),
  }), [chartColors, lang, metric, series, seriesMeta, theme, title]);

  if (loadError) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载失败' : 'Failed to load chart data'}
      </div>
    );
  }

  if (Object.keys(series).length === 0) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载中' : 'Loading chart data'}
      </div>
    );
  }

  const toolbar = (
    <>
      <div className="chart-toolbar">
        {availableMetrics.map((candidate) => (
          <button
            key={candidate}
            type="button"
            onClick={() => setMetric(candidate)}
            className={`chart-toggle ${metric === candidate ? 'chart-toggle-active' : ''}`}
          >
            {METRIC_LABELS[candidate][lang]}
          </button>
        ))}
      </div>
      {dateRange && <span className="chart-chip">{dateRange.start} → {dateRange.end}</span>}
    </>
  );

  const note = (
    <>
      {metric === 'weekly_equiv_cases'
        ? (lang === 'zh'
            ? '默认指标为周等价病例数，用于减弱不同报告频率导致的比较偏差。'
            : 'Default metric uses weekly-equivalent cases to reduce comparability bias from differing reporting frequencies.')
        : metric === 'incidence_rates'
          ? (lang === 'zh'
              ? '发病率按每 10 万人口标准化，适合进行跨国家或跨时期强度比较。'
              : 'Incidence rate is standardised per 100k population for cross-country and cross-period comparisons.')
          : (lang === 'zh'
              ? '当前视图显示原始报告值。图例已移出绘图区，避免遮挡曲线。'
              : 'This view shows raw reported values. The legend is kept outside the plot area to avoid overlap.')}
      {activeIds.length > topN && (
        <span>
          {' '}
          {lang === 'zh'
            ? `当前已选择 ${activeIds.length} 条疾病序列；若曲线过密，可在全屏侧栏中进一步筛选。`
            : `You currently have ${activeIds.length} disease series selected; narrow the full-screen sidebar selection if the chart feels too dense.`}
        </span>
      )}
      {metric === 'incidence_rates' && incidenceFallbackPoints > 0 && (
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
      {seriesMeta.map((item) => (
        <div className="chart-legend-item" key={item.id}>
          <div className="chart-legend-row">
            <span
              className="chart-legend-swatch"
              style={{
                backgroundColor: item.color,
              }}
            />
            <div>
              <div className="chart-legend-name">{item.name}</div>
              <div className="chart-legend-meta">
                {lang === 'zh' ? '最新值' : 'Latest'} {formatCellValue(item.latestValue, metric === 'incidence_rates' ? 2 : 0)}
                {' · '}
                {lang === 'zh' ? '累计' : 'Total'} {item.totalCases.toLocaleString()}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );

  const fullscreenSidebar = (
    <div className="chart-sidebar">
      <div className="chart-sidebar-header">
        <div>
          <div className="chart-sidebar-title">
            {lang === 'zh' ? '疾病筛选' : 'Disease filter'}
          </div>
          <div className="chart-sidebar-copy">
            {lang === 'zh'
              ? '全屏模式下可搜索并勾选疾病，右侧列表会持续同步当前图中的序列。'
              : 'Search and tick diseases in full-screen mode. The list always reflects the series currently visible in the chart.'}
          </div>
        </div>
        <span className="chart-chip whitespace-nowrap">
          {lang === 'zh' ? `已选 ${activeIds.length}` : `${activeIds.length} selected`}
        </span>
      </div>

      <input
        type="search"
        value={sidebarQuery}
        onChange={(event) => setSidebarQuery(event.target.value)}
        placeholder={lang === 'zh' ? '搜索疾病…' : 'Search diseases…'}
        className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
      />

      <div className="chart-toolbar">
        <button type="button" onClick={resetToTopDiseases} className="chart-toggle">
          {lang === 'zh' ? '回到前 10' : 'Reset to top 10'}
        </button>
        <button
          type="button"
          onClick={selectVisibleDiseases}
          className="chart-toggle"
          disabled={sidebarDiseaseIds.length === 0}
        >
          {lang === 'zh' ? '选择当前结果' : 'Select visible'}
        </button>
      </div>

      <div className="chart-sidebar-list">
        {sidebarDiseaseIds.length === 0 ? (
          <div className="chart-sidebar-empty">
            {lang === 'zh' ? '没有匹配的疾病。' : 'No diseases matched your search.'}
          </div>
        ) : (
          sidebarDiseaseIds.map((id) => {
            const item = series[id];
            const values = metric === 'incidence_rates'
              ? item.incidence_rates
              : metric === 'weekly_equiv_cases'
                ? item.weekly_equiv_cases
                : item[metric];
            const latestValue = [...values].reverse().find((value) => value != null) ?? null;
            const isActive = activeIds.includes(id);
            const seriesColor = isActive ? seriesColorMap.get(id) : undefined;
            const totalCases = item.total_cases ?? 0;
            const volumePercent = totalCasesRange.max <= totalCasesRange.min
              ? (totalCasesRange.max === 0 ? 0 : 100)
              : ((totalCases - totalCasesRange.min) / (totalCasesRange.max - totalCasesRange.min)) * 100;
            const itemStyle = {
              ['--chart-sidebar-volume' as string]: `${Math.max(0, Math.min(100, volumePercent))}%`,
            } as CSSProperties;

            return (
              <label
                key={id}
                className={`chart-sidebar-item ${isActive ? 'chart-sidebar-item-active' : ''}`}
                style={itemStyle}
              >
                <div className="chart-sidebar-item-inner flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={isActive}
                    onChange={() => toggleDiseaseSelection(id)}
                    className="chart-sidebar-checkbox mt-1"
                    style={seriesColor ? { accentColor: seriesColor } : undefined}
                  />
                  <span
                    aria-hidden="true"
                    className={`chart-sidebar-swatch ${seriesColor ? 'chart-sidebar-swatch-active' : ''}`}
                    style={seriesColor ? { backgroundColor: seriesColor, borderColor: seriesColor } : undefined}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="chart-sidebar-name">
                      {lang === 'zh' ? item.name_zh : item.name_en}
                    </div>
                    <div className="chart-sidebar-meta">
                      {lang === 'zh' ? '最新值' : 'Latest'} {formatCellValue(latestValue, metric === 'incidence_rates' ? 2 : 0)}
                      {' · '}
                      {lang === 'zh' ? '累计' : 'Total'} {totalCases.toLocaleString()}
                    </div>
                  </div>
                </div>
              </label>
            );
          })
        )}
      </div>
    </div>
  );

  const table = (
    <>
      <div className="data-preview-meta">
        {lang === 'zh'
          ? `原始数据预览，共 ${tableRows.length} 行。当前表格显示 ${METRIC_LABELS[metric][lang]}。`
          : `Raw data preview with ${tableRows.length} rows. The current table shows ${METRIC_LABELS[metric][lang]}.`}
      </div>
      <table className="data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">{lang === 'zh' ? '日期' : 'Date'}</th>
            {seriesMeta.map((item) => (
              <th key={item.id}>{item.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tableRows.map((row) => (
            <tr key={row.date}>
              <td className="is-sticky">{row.date}</td>
              {row.values.map((value, index) => (
                <td key={`${row.date}-${seriesMeta[index].id}`}>{formatCellValue(value, metric === 'incidence_rates' ? 2 : 0)}</td>
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
        <EChartsReact
          echarts={echarts}
          option={option}
          notMerge
          style={{
            width: '100%',
            height: isFullscreen ? '100%' : height,
          }}
        />
      )}
      table={table}
      legend={legend}
      fullscreenSidebar={fullscreenSidebar}
      sourceMeta={sourceMeta}
    />
  );
}
