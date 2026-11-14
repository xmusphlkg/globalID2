import type { EChartsOption } from 'echarts';
import type { Burst } from './types.ts';
import { escapeHtml, percentPoints, hotspotColors, hotspotLabels } from './presentation.ts';

export function buildBurstOption({ bursts, colors, lang }: { bursts: Burst[]; colors: ReturnType<typeof hotspotColors>; lang: 'en' | 'zh' | 'fr' }): EChartsOption {
const { t, topicLabel, periodLabel } = hotspotLabels(lang);

    const yLabels = bursts.map((burst) => `${periodLabel(burst)} · ${topicLabel(burst.topic)}`);
    return {
      backgroundColor: 'transparent',
      color: colors.palette,
      aria: {
        enabled: true,
        label: { description: t('Ranked monthly topic burst events.', '按月度主题升温事件排序的图表。', 'Classement mensuel des accélérations de thèmes.') },
      },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        confine: true,
        backgroundColor: colors.popover,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (params: any) => {
          const item = Array.isArray(params) ? params[0] : params;
          const burst = bursts[item?.dataIndex ?? 0];
          if (!burst) return '';
          return `<strong>${escapeHtml(topicLabel(burst.topic))}</strong><br/>${escapeHtml(periodLabel(burst))} · ${burst.count} ${t('papers', '篇论文', 'publications')}<br/>${t('Growth', '增长', 'Croissance')} : ${burst.growth >= 0 ? '+' : ''}${burst.growth} · ${t('Share move', '占比变化', 'Variation de part')} : ${percentPoints(burst.share_delta)}<br/>${t('Burst score', '爆发分', 'Score d’accélération')} : ${burst.burst_score}`;
        },
      },
      grid: { top: 22, right: 112, bottom: 48, left: 230 },
      xAxis: {
        type: 'value',
        name: t('Burst score', '爆发分', 'Score d’accélération'),
        nameTextStyle: { color: colors.faint },
        axisLabel: { color: colors.faint },
        splitLine: { lineStyle: { color: colors.line, opacity: 0.5 } },
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: yLabels,
        axisLabel: { color: colors.muted, width: 210, overflow: 'truncate' },
        axisTick: { show: false },
        axisLine: { lineStyle: { color: colors.line } },
      },
      dataZoom: bursts.length > 8 ? [
        { type: 'inside', yAxisIndex: 0, filterMode: 'none' },
      ] : [],
      toolbox: {
        right: 8,
        top: 0,
        feature: { restore: {}, saveAsImage: { pixelRatio: 2 } },
        iconStyle: { borderColor: colors.faint },
      },
      series: [{
        type: 'bar',
        data: bursts.map((burst, index) => ({
          value: burst.burst_score,
          itemStyle: { color: colors.palette[index % colors.palette.length] },
          label: {
            show: true,
            position: 'right',
            color: colors.text,
            formatter: `${burst.count} · ${burst.growth >= 0 ? '+' : ''}${burst.growth}`,
          },
        })),
        barMaxWidth: 20,
      }],
    };

}
