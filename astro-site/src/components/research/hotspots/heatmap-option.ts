import type { EChartsOption } from 'echarts';
import type { HotspotPeriod, HeatmapRow } from './types.ts';
import { escapeHtml, hotspotColors, hotspotLabels } from './presentation.ts';

export function buildHeatmapOption({ colors, heatmapPeriods, heatmapRows, lang, theme }: { colors: ReturnType<typeof hotspotColors>; heatmapPeriods: HotspotPeriod[]; heatmapRows: HeatmapRow[]; lang: 'en' | 'zh' | 'fr'; theme: 'light' | 'dark' }): EChartsOption {
const { t, topicLabel, diseaseLabel, periodLabel } = hotspotLabels(lang);

    const xLabels = heatmapPeriods.map(periodLabel);
    const yLabels = heatmapRows.map((row) => `${diseaseLabel(row)} · ${topicLabel(row.topic)}`);
    const maxValue = Math.max(1, ...heatmapRows.flatMap((row) => row.cells.map((cell) => cell.count)));
    const periodIndex = new Map(heatmapPeriods.map((period, index) => [period.period, index]));
    const data: [number, number, number][] = heatmapRows.flatMap((row, rowIndex) => (
      row.cells.map((cell) => [periodIndex.get(cell.period) ?? 0, rowIndex, cell.count] as [number, number, number])
    ));
    return {
      backgroundColor: 'transparent',
      color: colors.palette,
      aria: {
        enabled: true,
        decal: { show: false },
        label: { description: t('Heatmap of high-confidence disease and topic combinations over time.', '按时间展示高置信疾病与主题组合的热力矩阵。', 'Carte thermique des associations à forte confiance entre maladies et thèmes au fil du temps.') },
      },
      tooltip: {
        confine: true,
        backgroundColor: colors.popover,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (param: any) => {
          const value = param?.data ?? [];
          const row = heatmapRows[value[1]] as HeatmapRow | undefined;
          return `<strong>${escapeHtml(row ? `${diseaseLabel(row)} · ${topicLabel(row.topic)}` : '')}</strong><br/>${escapeHtml(xLabels[value[0]] ?? '')}: ${value[2] ?? 0} ${t('papers', '篇论文', 'publications')}`;
        },
      },
      grid: { top: 24, right: 42, bottom: 112, left: 240 },
      xAxis: {
        type: 'category',
        data: xLabels,
        axisLabel: { color: colors.faint, rotate: 35 },
        axisLine: { lineStyle: { color: colors.line } },
        axisTick: { lineStyle: { color: colors.line } },
      },
      yAxis: {
        type: 'category',
        data: yLabels,
        inverse: true,
        axisLabel: { color: colors.muted, width: 218, overflow: 'truncate' },
        axisLine: { lineStyle: { color: colors.line } },
        axisTick: { show: false },
      },
      visualMap: {
        min: 0,
        max: maxValue,
        orient: 'horizontal',
        left: 'center',
        bottom: 14,
        calculable: true,
        textStyle: { color: colors.muted },
        inRange: { color: theme === 'dark' ? ['#16283a', '#2d6574', '#76b7b2', '#f0c16a'] : ['#eef5f7', '#b8d8d7', '#2a8f87', '#d99121'] },
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, filterMode: 'none' },
      ],
      toolbox: {
        right: 8,
        top: 0,
        feature: { restore: {}, saveAsImage: { pixelRatio: 2 } },
        iconStyle: { borderColor: colors.faint },
      },
      series: [{
        type: 'heatmap',
        data,
        label: { show: true, color: colors.text, formatter: (param: any) => param.value?.[2] ? String(param.value[2]) : '' },
        itemStyle: { borderColor: theme === 'dark' ? '#1c3145' : '#f7f4ed', borderWidth: 1 },
        emphasis: { itemStyle: { borderColor: colors.text, borderWidth: 1 } },
      }],
    };

}
