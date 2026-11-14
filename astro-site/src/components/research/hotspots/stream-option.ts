import type { EChartsOption } from 'echarts';
import type { HotspotPeriod, StreamSeries } from './types.ts';
import { escapeHtml, periodDate, hotspotColors, hotspotLabels } from './presentation.ts';

export function buildStreamOption({ colors, lang, streamPeriods, streamSeries }: { colors: ReturnType<typeof hotspotColors>; lang: 'en' | 'zh' | 'fr'; streamPeriods: HotspotPeriod[]; streamSeries: StreamSeries[] }): EChartsOption {
const { t, topicLabel } = hotspotLabels(lang);

    const pointLookup = new Map(streamSeries.flatMap((series) => (
      (series.points ?? []).map((point) => [`${series.topic}::${point.period}`, point] as const)
    )));
    const data: [string, number, string][] = streamPeriods.flatMap((period) => streamSeries.map((series) => {
      const point = pointLookup.get(`${series.topic}::${period.period}`);
      return [periodDate(period), point?.count ?? 0, topicLabel(series.topic)] as [string, number, string];
    }));
    return {
      backgroundColor: 'transparent',
      color: colors.palette,
      aria: {
        enabled: true,
        decal: { show: false },
        label: { description: t('Monthly theme river showing Research Radar topic attention.', '展示 Research Radar 主题关注度月度变化的主题河流图。', 'Rivière thématique mensuelle montrant l’attention bibliographique dans le Radar de recherche.') },
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: colors.popover,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (params: any) => {
          const rows = Array.isArray(params) ? params : [params];
          const sorted = rows
            .map((item: any) => ({
              topic: item?.data?.[2] ?? item?.seriesName,
              count: Number(item?.data?.[1] ?? 0),
              marker: item?.marker ?? '',
            }))
            .filter((item) => item.count > 0)
            .sort((a, b) => b.count - a.count)
            .slice(0, 8);
          if (!sorted.length) return t('No topic mentions in this month.', '该月没有主题记录。', 'Aucune mention de thème ce mois-ci.');
          return `<strong>${escapeHtml(rows[0]?.axisValueLabel ?? rows[0]?.name)}</strong><br/>${sorted.map((item) => `${item.marker}${escapeHtml(item.topic)}: ${item.count}`).join('<br/>')}`;
        },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { color: colors.muted },
        pageTextStyle: { color: colors.muted },
        data: streamSeries.map((series) => topicLabel(series.topic)),
      },
      singleAxis: {
        type: 'time',
        top: 34,
        bottom: 92,
        left: 36,
        right: 36,
        axisLine: { lineStyle: { color: colors.line } },
        axisTick: { lineStyle: { color: colors.line } },
        splitLine: { show: true, lineStyle: { color: colors.line, opacity: 0.4 } },
        axisLabel: { color: colors.faint },
      },
      series: [{
        type: 'themeRiver',
        data,
        emphasis: { focus: 'series' },
        label: { show: false },
        itemStyle: { opacity: 0.78 },
      }],
      dataZoom: [
        { type: 'inside', singleAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: true },
        { type: 'slider', singleAxisIndex: 0, height: 18, bottom: 30, borderColor: colors.line, textStyle: { color: colors.faint }, brushSelect: true },
      ],
      toolbox: {
        right: 8,
        top: 4,
        feature: { restore: {}, saveAsImage: { pixelRatio: 2 } },
        iconStyle: { borderColor: colors.faint },
      },
    };

}
