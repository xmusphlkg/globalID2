import React, { useEffect, useMemo, useState } from 'react';

type Lang = 'en' | 'zh';
type AnyRecord = Record<string, any>;

interface Props {
  report: AnyRecord;
}

function useLang(): Lang {
  const [lang, setLang] = useState<Lang>(() => {
    if (typeof window === 'undefined') return 'en';
    return (localStorage.getItem('lang') as Lang) || 'en';
  });

  useEffect(() => {
    const update = () => setLang((localStorage.getItem('lang') as Lang) || 'en');
    window.addEventListener('storage', update);
    document.addEventListener('globalid:language-change', update);
    return () => {
      window.removeEventListener('storage', update);
      document.removeEventListener('globalid:language-change', update);
    };
  }, []);

  return lang;
}

function asArray(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

function num(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function fmt(value: unknown, digits = 0): string {
  const parsed = num(value);
  if (parsed === null) return '—';
  return parsed.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function percent(score: unknown): string {
  const parsed = num(score);
  return parsed === null ? '—' : `${Math.round(parsed * 100)}%`;
}

function rollingMean(values: Array<number | null>, windowSize: number): Array<number | null> {
  return values.map((_value, index) => {
    const windowValues = values
      .slice(Math.max(0, index - windowSize + 1), index + 1)
      .filter((item): item is number => item !== null && Number.isFinite(item));
    if (windowValues.length < 2) return null;
    return Math.round((windowValues.reduce((sum, item) => sum + item, 0) / windowValues.length) * 100) / 100;
  });
}

function seriesFor(figure: AnyRecord, figureData: AnyRecord): AnyRecord {
  const key = figure.data_key || `disease:${figure.disease_id}`;
  return (figureData.series || {})[key] || {};
}

function riskColor(level: unknown): string {
  return ({
    critical: '#991b1b',
    high: '#b91c1c',
    moderate: '#b45309',
    low: '#0f766e',
  } as Record<string, string>)[String(level || 'low').toLowerCase()] || '#0f766e';
}

function signalContextOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const cases = asArray(data.cases).map(num);
  const visual = data.visual || {};
  const latest = cases.length ? cases[cases.length - 1] : null;
  const rows = [
    { label: lang === 'zh' ? '最新期病例' : 'Latest cases', value: latest, color: '#116a8c' },
    { label: lang === 'zh' ? '上一期病例' : 'Previous cases', value: visual.previous_cases, color: '#64748b' },
    { label: lang === 'zh' ? '最新期前中位数' : 'Pre-latest median', value: visual.pre_latest_median_cases, color: '#0f766e' },
    { label: lang === 'zh' ? '3期滚动均值' : '3-period mean', value: visual.rolling_mean_cases, color: '#b45309' },
    { label: lang === 'zh' ? '最近4期病例' : 'Latest 4 periods', value: visual.latest_4_period_cases, color: '#b91c1c' },
    { label: lang === 'zh' ? '前4期病例' : 'Previous 4 periods', value: visual.previous_4_period_cases, color: '#94a3b8' },
  ].filter((row) => num(row.value) !== null).reverse();
  if (!rows.length) return null;
  const subtitle = [
    visual.last4_change_pct == null ? null : `${lang === 'zh' ? '近4期变化 ' : '4-period change '}${fmt(visual.last4_change_pct, 1)}%`,
    visual.latest_to_baseline_ratio == null ? null : `${lang === 'zh' ? '最新/基线 ' : 'latest/baseline '}${fmt(visual.latest_to_baseline_ratio, 2)}x`,
    visual.anomaly?.robust_z == null ? null : `MAD z ${fmt(visual.anomaly.robust_z, 2)}`,
  ].filter(Boolean).join(' · ');
  return {
    animation: false,
    backgroundColor: 'transparent',
    color: ['#116a8c', '#b45309', '#b91c1c', '#0f766e'],
    title: subtitle ? { text: subtitle, left: 4, top: 0, textStyle: { color: '#94a3b8', fontSize: 12, fontWeight: 500 } } : undefined,
    tooltip: { trigger: 'item', confine: true },
    grid: { left: 142, right: 28, top: subtitle ? 42 : 18, bottom: 38 },
    xAxis: { type: 'value', name: lang === 'zh' ? '病例数' : 'Cases', min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    yAxis: { type: 'category', data: rows.map((row) => row.label), axisLabel: { color: '#94a3b8' } },
    series: [{ type: 'bar', barMaxWidth: 18, data: rows.map((row) => ({ value: Number(row.value), itemStyle: { color: row.color } })), label: { show: true, position: 'right', color: '#cbd5e1' } }],
  };
}

function epidemicCurveOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const periods = asArray(data.periods).map(String);
  const cases = asArray(data.cases).map(num);
  if (periods.length < 2) return null;
  const visual = data.visual || {};
  const chartSeries: AnyRecord[] = [{
    name: lang === 'zh' ? '报告病例' : 'Reported cases',
    type: 'line',
    data: cases,
    symbol: 'circle',
    symbolSize: 6,
    lineStyle: { width: 2.5, color: '#116a8c' },
    itemStyle: { color: '#116a8c' },
    markLine: num(visual.pre_latest_median_cases) === null ? undefined : {
      silent: true,
      symbol: 'none',
      lineStyle: { type: 'dashed', color: '#94a3b8', width: 1.5 },
      data: [{ yAxis: num(visual.pre_latest_median_cases) }],
    },
  }];
  const mean = rollingMean(cases, 3);
  if (mean.some((item) => item !== null)) {
    chartSeries.push({ name: lang === 'zh' ? '3期均值' : '3-period mean', type: 'line', data: mean, symbol: 'none', connectNulls: true, lineStyle: { width: 2, type: 'dotted', color: '#b45309' } });
  }
  const peakIndex = periods.indexOf(String(visual.peak_period || ''));
  if (peakIndex >= 0 && num(visual.peak_cases) !== null) {
    chartSeries.push({ name: lang === 'zh' ? '观察峰值' : 'Observed peak', type: 'scatter', data: [[peakIndex, num(visual.peak_cases)]], symbol: 'diamond', symbolSize: 12, itemStyle: { color: '#b91c1c' } });
  }
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', confine: true },
    legend: { top: 0, right: 0, textStyle: { color: '#94a3b8' } },
    grid: { left: 54, right: 22, top: 48, bottom: 74 },
    xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#94a3b8' } },
    yAxis: { type: 'value', name: lang === 'zh' ? '病例数' : 'Cases', min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    series: chartSeries,
  };
}

function heatmapOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const periods = asArray(data.periods).map(String).slice(-52);
  const cases = asArray(data.cases).map(num).slice(-52);
  if (periods.length < 4) return null;
  const columns = periods.length >= 13 ? 13 : Math.max(periods.length, 1);
  const rows = Math.ceil(periods.length / columns);
  const cells = periods.map((period, index) => [index % columns, Math.floor(index / columns), cases[index] ?? 0, period]);
  const maxValue = cells.reduce((max, item) => Math.max(max, Number(item[2]) || 0), 0);
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', confine: true },
    grid: { left: 70, right: 72, top: 18, bottom: 54 },
    xAxis: { type: 'category', data: Array.from({ length: columns }, (_value, index) => String(index + 1)), name: lang === 'zh' ? '近期报告期序列' : 'Sequential recent periods', axisLabel: { color: '#94a3b8' } },
    yAxis: { type: 'category', data: Array.from({ length: rows }, (_value, index) => lang === 'zh' ? `分块 ${index + 1}` : `Block ${index + 1}`), axisLabel: { color: '#94a3b8' } },
    visualMap: { min: 0, max: maxValue, calculable: true, orient: 'vertical', right: 8, top: 28, inRange: { color: ['#17283a', '#9ecae1', '#b91c1c'] }, textStyle: { color: '#94a3b8' } },
    series: [{ type: 'heatmap', data: cells, label: { show: false } }],
  };
}

function casesIncidenceOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const periods = asArray(data.periods).map(String);
  const cases = asArray(data.cases).map(num);
  const incidence = asArray(data.incidence_rate_per_100k).map(num);
  if (periods.length < 2 || !incidence.some((item) => item !== null)) return null;
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', confine: true },
    legend: { top: 0, right: 0, textStyle: { color: '#94a3b8' } },
    grid: [{ left: 58, right: 24, top: 48, height: '40%' }, { left: 58, right: 24, bottom: 70, height: '30%' }],
    xAxis: [{ type: 'category', data: periods, gridIndex: 0, axisLabel: { show: false } }, { type: 'category', data: periods, gridIndex: 1, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#94a3b8' } }],
    yAxis: [{ type: 'value', name: lang === 'zh' ? '病例数' : 'Cases', gridIndex: 0, min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } }, { type: 'value', name: lang === 'zh' ? '每10万人' : 'Per 100k', gridIndex: 1, min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } }],
    series: [{ name: lang === 'zh' ? '报告病例' : 'Reported cases', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: cases, itemStyle: { color: '#116a8c', opacity: 0.9 } }, { name: lang === 'zh' ? '每10万人粗发病率' : 'Crude incidence per 100k', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: incidence, lineStyle: { color: '#b45309', width: 2.5 }, itemStyle: { color: '#b45309' } }],
  };
}

function riskRankingOption(figureData: AnyRecord, lang: Lang) {
  const rows = asArray(figureData.risk_ranking).slice(0, 10).reverse();
  if (rows.length < 2) return null;
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', confine: true },
    grid: { left: 150, right: 24, top: 20, bottom: 44 },
    xAxis: { type: 'value', name: lang === 'zh' ? '风险分' : 'Risk score', min: 0, max: 100, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    yAxis: { type: 'category', data: rows.map((row) => row.name || 'Unknown'), axisLabel: { color: '#94a3b8' } },
    series: [{ type: 'bar', barMaxWidth: 18, data: rows.map((row) => ({ value: Number(row.risk_score || 0), itemStyle: { color: riskColor(row.risk_level) } })) }],
  };
}

function seasonalBaselineBandOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const periods = asArray(data.periods).map(String);
  const cases = asArray(data.cases).map(num);
  if (periods.length < 2) return null;
  const visual = data.visual || {};
  const derived = visual.derived || {};
  const lower = asArray(derived.baseline_lower).map(num);
  const upper = asArray(derived.baseline_upper).map(num);
  const bandWidth = upper.map((value, index) => (value === null || lower[index] === null ? null : Math.max(0, value - Number(lower[index]))));
  const chartSeries: AnyRecord[] = [
    { name: lang === 'zh' ? '背景带下界' : 'Baseline lower', type: 'line', data: lower, stack: 'baseline-band', symbol: 'none', lineStyle: { opacity: 0 }, itemStyle: { opacity: 0 }, emphasis: { disabled: true } },
    { name: lang === 'zh' ? '背景带' : 'Baseline band', type: 'line', data: bandWidth, stack: 'baseline-band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: 'rgba(17,106,140,0.14)' }, itemStyle: { opacity: 0 }, emphasis: { disabled: true } },
    { name: lang === 'zh' ? '报告病例' : 'Reported cases', type: 'line', data: cases, symbol: 'circle', symbolSize: 5, lineStyle: { width: 2.5, color: '#116a8c' }, itemStyle: { color: '#116a8c' } },
    { name: lang === 'zh' ? '3期均值' : '3-period mean', type: 'line', data: asArray(derived.rolling_mean_3).map(num), symbol: 'none', connectNulls: true, lineStyle: { width: 2, type: 'dotted', color: '#b45309' } },
  ];
  if (num(visual.pre_latest_median_cases) !== null) {
    chartSeries[2].markLine = { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#64748b' }, data: [{ yAxis: num(visual.pre_latest_median_cases) }] };
  }
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', confine: true },
    legend: { top: 0, right: 0, textStyle: { color: '#94a3b8' } },
    grid: { left: 56, right: 24, top: 50, bottom: 74 },
    xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#94a3b8' } },
    yAxis: { type: 'value', name: lang === 'zh' ? '病例数' : 'Cases', min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    series: chartSeries,
  };
}

function anomalyMarkerCurveOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const periods = asArray(data.periods).map(String);
  const cases = asArray(data.cases).map(num);
  if (periods.length < 4) return null;
  const visual = data.visual || {};
  const derived = visual.derived || {};
  const latestIndex = Math.max(0, periods.length - 1);
  const peakIndex = periods.indexOf(String(visual.peak_period || ''));
  const threshold = asArray(derived.anomaly_threshold).map(num).find((item) => item !== null);
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', confine: true },
    legend: { top: 0, right: 0, textStyle: { color: '#94a3b8' } },
    grid: { left: 56, right: 24, top: 50, bottom: 74 },
    xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#94a3b8' } },
    yAxis: { type: 'value', name: lang === 'zh' ? '病例数' : 'Cases', min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    series: [
      {
        name: lang === 'zh' ? '报告病例' : 'Reported cases',
        type: 'line',
        data: cases,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { width: 2.4, color: '#116a8c' },
        itemStyle: { color: '#116a8c' },
        markLine: threshold === undefined ? undefined : { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#b91c1c', width: 1.5 }, label: { formatter: lang === 'zh' ? '异常阈值' : 'alert threshold' }, data: [{ yAxis: threshold }] },
      },
      { name: lang === 'zh' ? '最新期' : 'Latest', type: 'scatter', data: [[latestIndex, Number(cases[latestIndex] || 0)]], symbolSize: 13, itemStyle: { color: '#b91c1c' } },
      { name: lang === 'zh' ? '峰值' : 'Peak', type: 'scatter', data: peakIndex >= 0 ? [[peakIndex, num(visual.peak_cases) ?? Number(cases[peakIndex] || 0)]] : [], symbol: 'diamond', symbolSize: 13, itemStyle: { color: '#b45309' } },
    ],
  };
}

function dataQualityTimelineOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  const data = seriesFor(figure, figureData);
  const periods = asArray(data.periods).map(String);
  if (periods.length < 2) return null;
  const availability = data.visual?.derived?.availability || {};
  const rows = [
    { key: 'cases', label: lang === 'zh' ? '病例' : 'Cases' },
    { key: 'deaths', label: lang === 'zh' ? '死亡' : 'Deaths' },
    { key: 'incidence_rate_per_100k', label: lang === 'zh' ? '粗发病率' : 'Crude incidence' },
  ];
  const cells = rows.flatMap((row, rowIndex) => {
    const values = asArray(availability[row.key]).map(num);
    return periods.map((period, index) => [index, rowIndex, Number(values[index] || 0), period, row.label]);
  });
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', confine: true },
    grid: { left: 96, right: 32, top: 20, bottom: 64 },
    xAxis: { type: 'category', data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: '#94a3b8' } },
    yAxis: { type: 'category', data: rows.map((row) => row.label), axisLabel: { color: '#94a3b8' } },
    visualMap: { show: false, min: 0, max: 1, inRange: { color: ['#334155', '#0f766e'] } },
    series: [{ name: lang === 'zh' ? '可用性' : 'Availability', type: 'heatmap', data: cells, label: { show: false } }],
  };
}

function riskMatrixOption(figureData: AnyRecord, lang: Lang) {
  const rows = asArray(figureData.risk_ranking).slice(0, 12);
  if (rows.length < 2) return null;
  const maxScore = rows.reduce((max, row) => Math.max(max, Number(row.risk_score || 0)), 1);
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'item', confine: true },
    grid: { left: 68, right: 28, top: 30, bottom: 64 },
    xAxis: { type: 'value', name: lang === 'zh' ? '最新病例' : 'Latest cases', min: 0, axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    yAxis: { type: 'value', name: lang === 'zh' ? '较上一期变化(%)' : 'Change vs previous (%)', axisLabel: { color: '#94a3b8' }, splitLine: { lineStyle: { color: '#2a3f59', type: 'dashed' } } },
    series: [{
      name: lang === 'zh' ? '疾病' : 'Disease',
      type: 'scatter',
      data: rows.map((row) => ({ name: row.name || 'Unknown', value: [Number(row.latest_cases || 0), Number(row.change_pct || 0)], risk_score: Number(row.risk_score || 0), itemStyle: { color: riskColor(row.risk_level) } })),
      symbolSize: (_value: unknown, params: AnyRecord) => 10 + (Number(params?.data?.risk_score || 0) / maxScore) * 28,
      label: { show: true, formatter: (params: AnyRecord) => params?.data?.name || '', color: '#cbd5e1' },
    }],
  };
}

function buildOption(figure: AnyRecord, figureData: AnyRecord, lang: Lang) {
  if (figure.figure_type === 'signal_context_panel') return signalContextOption(figure, figureData, lang);
  if (figure.figure_type === 'epidemic_curve') return epidemicCurveOption(figure, figureData, lang);
  if (figure.figure_type === 'recent_window_heatmap') return heatmapOption(figure, figureData, lang);
  if (figure.figure_type === 'cases_incidence_panel') return casesIncidenceOption(figure, figureData, lang);
  if (figure.figure_type === 'risk_ranking_bar') return riskRankingOption(figureData, lang);
  if (figure.figure_type === 'seasonal_baseline_band') return seasonalBaselineBandOption(figure, figureData, lang);
  if (figure.figure_type === 'anomaly_marker_curve') return anomalyMarkerCurveOption(figure, figureData, lang);
  if (figure.figure_type === 'data_quality_timeline') return dataQualityTimelineOption(figure, figureData, lang);
  if (figure.figure_type === 'risk_matrix') return riskMatrixOption(figureData, lang);
  return null;
}

function LazyEChart({ option, height }: { option: AnyRecord; height: number }) {
  const [modules, setModules] = useState<{ EChartsReact: any; echarts: any } | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([
      import('echarts-for-react/lib/core'),
      import('../../lib/echarts'),
    ]).then(([reactModule, echartsModule]) => {
      if (active) {
        setModules({
          EChartsReact: reactModule.default,
          echarts: echartsModule.default,
        });
      }
    });
    return () => {
      active = false;
    };
  }, []);

  if (!modules) {
    return (
      <div
        className="flex items-center justify-center border border-dashed border-slate-700 bg-slate-950/50 text-xs uppercase tracking-[0.14em] text-slate-500"
        style={{ width: '100%', height }}
      >
        Loading figure
      </div>
    );
  }

  const Chart = modules.EChartsReact;
  return <Chart echarts={modules.echarts} option={option} notMerge style={{ width: '100%', height }} />;
}

function FigureCard({ figure, figureData, lang }: { figure: AnyRecord; figureData: AnyRecord; lang: Lang }) {
  const option = useMemo(() => buildOption(figure, figureData, lang), [figure, figureData, lang]);
  if (!option) return null;
  const height = Number(figure.height || 360);
  return (
    <figure className="border border-slate-700/70 bg-slate-900/40">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-300">
            {figure.number ? `Figure ${figure.number}` : 'Figure'}
          </p>
          <h3 className="mt-1 text-sm font-semibold text-slate-100">{figure.title}</h3>
        </div>
        <span className="text-[11px] uppercase tracking-[0.14em] text-slate-500">
          {String(figure.figure_type || '').replaceAll('_', ' ')}
        </span>
      </div>
      <div className="px-3 py-3">
        <LazyEChart option={option} height={height} />
      </div>
      <figcaption className="border-t border-slate-800 px-4 py-3 text-xs leading-5 text-slate-400">
        {figure.caption && <p>{figure.caption}</p>}
        {asArray(figure.legend).length > 0 && (
          <ul className="mt-2 list-disc space-y-1 pl-4 text-slate-500">
            {asArray(figure.legend).map((item) => <li key={String(item)}>{String(item)}</li>)}
          </ul>
        )}
      </figcaption>
    </figure>
  );
}

export default function AnalyticalReportV3Panel({ report }: Props) {
  const lang = useLang();
  const metadata = report.metadata || {};
  const qualityGate = report.quality_gate || metadata.quality_gate || {};
  const dataQuality = report.data_quality || metadata.data_quality || {};
  const summaryMetrics = metadata.summary_metrics || {};
  const riskRanking = asArray(metadata.risk_ranking).slice(0, 8);
  const figures = asArray(metadata.figures);
  const figureData = metadata.figure_data || {};
  const sections = asArray(report.sections);
  const passed = qualityGate.passed === true;

  return (
    <div className="space-y-8">
      <section className="figure-panel">
        <p className="figure-kicker" data-lang-en="Evidence package" data-lang-zh="证据包">Evidence package</p>
        <h2 className="figure-title" data-lang-en="Quality gate and priority signals" data-lang-zh="质量门与优先信号">
          Quality gate and priority signals
        </h2>
        <div className="mt-5 grid gap-3 sm:grid-cols-4">
          <div className="border border-slate-700/70 bg-slate-900/30 px-4 py-3">
            <div className="text-xs text-slate-500">{lang === 'zh' ? '质量门' : 'Quality gate'}</div>
            <div className={`mt-1 text-lg font-semibold ${passed ? 'text-emerald-300' : 'text-amber-300'}`}>
              {passed ? (lang === 'zh' ? '通过' : 'Passed') : (lang === 'zh' ? '需审核' : 'Review')}
            </div>
          </div>
          <div className="border border-slate-700/70 bg-slate-900/30 px-4 py-3">
            <div className="text-xs text-slate-500">{lang === 'zh' ? '门控分' : 'Gate score'}</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">{percent(qualityGate.overall_score)}</div>
          </div>
          <div className="border border-slate-700/70 bg-slate-900/30 px-4 py-3">
            <div className="text-xs text-slate-500">{lang === 'zh' ? '数据质量' : 'Data quality'}</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">{percent(dataQuality.score)}</div>
          </div>
          <div className="border border-slate-700/70 bg-slate-900/30 px-4 py-3">
            <div className="text-xs text-slate-500">{lang === 'zh' ? '方法版本' : 'Method'}</div>
            <div className="mt-1 truncate text-lg font-semibold text-slate-100">{report.method_version || metadata.method_version || '—'}</div>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <span className="rounded-none bg-slate-800 px-2.5 py-1 text-xs text-slate-300">{lang === 'zh' ? '病例' : 'Cases'} {fmt(summaryMetrics.total_cases)}</span>
          <span className="rounded-none bg-slate-800 px-2.5 py-1 text-xs text-slate-300">{lang === 'zh' ? '死亡' : 'Deaths'} {fmt(summaryMetrics.total_deaths)}</span>
          <span className="rounded-none bg-slate-800 px-2.5 py-1 text-xs text-slate-300">{lang === 'zh' ? '高风险' : 'High risk'} {fmt(summaryMetrics.high_risk_diseases)}</span>
        </div>
      </section>

      {riskRanking.length > 0 && (
        <section className="figure-panel">
          <p className="figure-kicker" data-lang-en="Risk ranking" data-lang-zh="风险排序">Risk ranking</p>
          <div className="overflow-hidden border border-slate-800">
            <table className="w-full text-sm">
              <thead className="bg-slate-900/60 text-xs text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left">#</th>
                  <th className="px-3 py-2 text-left">{lang === 'zh' ? '疾病' : 'Disease'}</th>
                  <th className="px-3 py-2 text-left">{lang === 'zh' ? '等级' : 'Level'}</th>
                  <th className="px-3 py-2 text-right">{lang === 'zh' ? '最新病例' : 'Latest'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800 text-slate-300">
                {riskRanking.map((row, index) => (
                  <tr key={`${row.disease_id}-${index}`}>
                    <td className="px-3 py-2">{index + 1}</td>
                    <td className="px-3 py-2 font-medium text-slate-100">{lang === 'zh' ? (row.name_zh || row.name_en) : (row.name_en || row.name_zh)}</td>
                    <td className="px-3 py-2">
                      <span className="inline-flex px-2 py-0.5 text-xs" style={{ color: riskColor(row.risk_level), border: `1px solid ${riskColor(row.risk_level)}55` }}>
                        {row.risk_level || '—'} · {fmt(row.risk_score)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">{fmt(row.latest_cases)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {figures.length > 0 && (
        <section className="figure-panel">
          <p className="figure-kicker" data-lang-en="Evidence figures" data-lang-zh="证据图形">Evidence figures</p>
          <h2 className="figure-title" data-lang-en="Visual diagnostics selected from the evidence packet" data-lang-zh="由证据包选择的可视化诊断">
            Visual diagnostics selected from the evidence packet
          </h2>
          <div className="mt-5 space-y-4">
            {figures.map((figure) => <FigureCard key={figure.id || figure.figure_type} figure={figure} figureData={figureData} lang={lang} />)}
          </div>
        </section>
      )}

      {sections.length > 0 && (
        <section className="figure-panel">
          <p className="figure-kicker" data-lang-en="Report text" data-lang-zh="报告正文">Report text</p>
          <div className="space-y-3">
            {sections.map((section) => (
              <details key={`${section.section_order}-${section.title}`} className="border border-slate-800 bg-slate-900/30">
                <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-slate-100">
                  {section.section_order}. {section.title || section.section_type}
                </summary>
                <div className="border-t border-slate-800 px-4 py-4 whitespace-pre-wrap text-sm leading-7 text-slate-400">
                  {section.content || ''}
                </div>
              </details>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
