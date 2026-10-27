import { MONTH_NAMES, type YearSummary } from './monthlyBarModel.ts';

export interface MonthlyBarChartColors {
  font: string;
  line: string;
  grid: string;
  hoverBg: string;
  hoverBorder: string;
  hoverFont: string;
  title: string;
}

interface BuildMonthlyBarOptionInput {
  summaries: YearSummary[];
  metricLabel: string;
  lang: 'en' | 'zh' | 'fr';
  colors: MonthlyBarChartColors;
  title?: string;
}

export const MONTHLY_BAR_REPLACE_MERGE: string[] = ['series'];

function formatValue(value: unknown) {
  if (value == null || value === '-') return '—';
  const numericValue = Number(value);
  return Number.isFinite(numericValue) ? numericValue.toLocaleString() : '—';
}

export function buildMonthlyBarOption({
  summaries,
  metricLabel,
  lang,
  colors,
  title,
}: BuildMonthlyBarOptionInput) {
  return {
    animationDuration: summaries.length > 12 ? 0 : 220,
    backgroundColor: 'transparent',
    title: title
      ? {
          text: title,
          textStyle: {
            color: colors.title,
            fontSize: 15,
            fontWeight: 600,
          },
          left: 0,
          top: 0,
        }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' as const },
      backgroundColor: colors.hoverBg,
      borderColor: colors.hoverBorder,
      borderWidth: 1,
      textStyle: { color: colors.hoverFont, fontSize: 12 },
      formatter: (params: any[]) => {
        const month = params[0]?.axisValueLabel ?? params[0]?.name ?? '';
        const monthIndex = params[0]?.dataIndex ?? -1;
        return [
          `<b>${month}</b>`,
          ...params.flatMap((param) => {
            const summary = summaries.find((item) => item.year === param.seriesName);
            const coverage = summary?.monthlyCoverage[monthIndex];
            const coverageStatus = coverage?.status === 'complete'
              ? (lang === 'zh' ? '完整' : lang === 'fr' ? 'complet' : 'complete')
              : coverage?.status === 'incompatible'
                ? (lang === 'zh' ? '来源定义不兼容' : lang === 'fr' ? 'définitions incompatibles' : 'incompatible definitions')
              : coverage?.status === 'no_data'
                ? (lang === 'zh' ? '无报告' : lang === 'fr' ? 'aucun rapport' : 'no reports')
                : (lang === 'zh' ? '部分' : lang === 'fr' ? 'partiel' : 'partial');
            return [
              `${param.marker}${param.seriesName}: <b>${formatValue(param.value)}</b>`,
              ...(coverage
                ? [lang === 'zh'
                  ? `报告地区：${coverage.reported}/${coverage.eligible}（${coverageStatus}）`
                  : lang === 'fr'
                    ? `Juridictions déclarantes : ${coverage.reported}/${coverage.eligible} (${coverageStatus})`
                    : `Reporting jurisdictions: ${coverage.reported}/${coverage.eligible} (${coverageStatus})`]
                : []),
            ];
          }),
        ].join('<br/>');
      },
    },
    grid: { left: 60, right: 18, top: title ? 38 : 16, bottom: 48 },
    xAxis: {
      type: 'category' as const,
      data: MONTH_NAMES,
      axisLabel: { color: colors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: colors.line } },
      axisTick: { lineStyle: { color: colors.line } },
    },
    yAxis: {
      type: 'value' as const,
      name: metricLabel,
      nameTextStyle: {
        color: colors.font,
        fontSize: 11,
        padding: [0, 0, 8, 0],
      },
      axisLabel: { color: colors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: colors.line } },
      axisTick: { lineStyle: { color: colors.line } },
      splitLine: {
        lineStyle: {
          color: colors.grid,
          type: 'dashed' as const,
        },
      },
      min: 0,
    },
    series: summaries.map((summary) => ({
      id: summary.year,
      name: summary.year,
      type: 'bar' as const,
      barMaxWidth: summaries.length <= 5 ? 22 : 16,
      itemStyle: { color: summary.color },
      emphasis: { focus: 'series' as const },
      data: summary.values,
    })),
  };
}
