import type { EChartsOption } from 'echarts';
import type { AlluvialPeriod, AlluvialTopic, AlluvialNode, AlluvialLink } from './types.ts';
import { escapeHtml, periodDisplay, hotspotColors, hotspotLabels } from './presentation.ts';

export function buildMigrationOption({ alluvialLinks, alluvialNodes, alluvialPeriodKeys, alluvialPeriods, alluvialTopics, colors, lang, theme }: { alluvialLinks: AlluvialLink[]; alluvialNodes: AlluvialNode[]; alluvialPeriodKeys: string[]; alluvialPeriods: AlluvialPeriod[]; alluvialTopics: AlluvialTopic[]; colors: ReturnType<typeof hotspotColors>; lang: 'en' | 'zh' | 'fr'; theme: 'light' | 'dark' }): EChartsOption {
const { t, topicLabel, periodLabel } = hotspotLabels(lang);

    const paletteByTopic = new Map(alluvialTopics.map((topic, index) => [topic.slug, colors.palette[index % colors.palette.length]]));
    const periodIndex = new Map(alluvialPeriodKeys.map((period, index) => [period, index]));
    const lastDepth = Math.max(0, alluvialPeriodKeys.length - 1);
    const nodeNames = new Set(alluvialNodes.map((node) => `${node.period}::${node.slug}`));
    const data = alluvialNodes.map((node) => {
      const depth = periodIndex.get(node.period) ?? 0;
      const isFirstPeriod = depth === 0;
      const isLastPeriod = depth === lastDepth;
      const labelPosition: 'left' | 'right' = isFirstPeriod ? 'left' : 'right';
      const labelAlign: 'left' | 'right' = isFirstPeriod ? 'right' : 'left';
      const labelVerticalAlign: 'middle' = 'middle';
      const labelOverflow: 'truncate' = 'truncate';
      const topicColor = paletteByTopic.get(node.slug) ?? colors.palette[0];
      return {
        name: `${node.period}::${node.slug}`,
        id: `${node.period}::${node.slug}`,
        value: Math.max(0.1, node.count || 0.1),
        depth,
        period: node.period,
        period_label: periodLabel({ period: node.period, label: periodDisplay(node.period, alluvialPeriods), start_date: alluvialPeriods.find((item) => item.period === node.period)?.start_date }),
        topic: topicLabel(node.topic),
        count: node.count,
        itemStyle: {
          color: topicColor,
          borderColor: theme === 'dark' ? 'rgba(255,255,255,.16)' : 'rgba(255,255,255,.88)',
          borderWidth: 0.8,
        },
        label: {
          show: isFirstPeriod || isLastPeriod,
          color: colors.text,
          position: labelPosition,
          align: labelAlign,
          verticalAlign: labelVerticalAlign,
          width: 128,
          overflow: labelOverflow,
          ellipsis: '...',
          lineHeight: 14,
          fontSize: 11,
          fontWeight: 650,
          formatter: topicLabel(node.topic),
        },
      };
    });
    const links = alluvialLinks
      .map((link) => ({
        source: `${link.source_period}::${link.slug}`,
        target: `${link.target_period}::${link.slug}`,
        value: Math.max(0.1, link.value || link.source_count || link.target_count || 0.1),
        topic: topicLabel(link.topic),
        source_period: link.source_period,
        target_period: link.target_period,
        source_count: link.source_count,
        target_count: link.target_count,
        lineStyle: { color: paletteByTopic.get(link.slug) ?? colors.palette[0], opacity: 0.24, curveness: 0.48 },
      }))
      .filter((link) => nodeNames.has(link.source) && nodeNames.has(link.target));
    return {
      backgroundColor: 'transparent',
      color: colors.palette,
      aria: {
        enabled: true,
        label: { description: t('Quarterly Sankey view of topic attention flow.', '按季度展示主题关注度流向的桑基图。', 'Vue de Sankey trimestrielle de l’évolution de l’attention par thème.') },
      },
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: colors.popover,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (param: any) => {
          const data = param?.data ?? {};
          if (param?.dataType === 'edge') {
            return `<strong>${escapeHtml(data.topic)}</strong><br/>${escapeHtml(periodLabel({ period: data.source_period }))} → ${escapeHtml(periodLabel({ period: data.target_period }))}<br/>${data.source_count ?? 0} → ${data.target_count ?? 0} ${t('mentions', '次提及', 'mentions')}`;
          }
          return `<strong>${escapeHtml(data.topic)}</strong><br/>${escapeHtml(data.period_label ?? periodLabel({ period: data.period }))} · ${data.count ?? 0} ${t('mentions', '次提及', 'mentions')}`;
        },
      },
      toolbox: {
        right: 8,
        top: 32,
        feature: { restore: {}, saveAsImage: { pixelRatio: 2 } },
        iconStyle: { borderColor: colors.faint },
      },
      series: [{
        type: 'sankey',
        data,
        links,
        top: 58,
        bottom: 26,
        left: '16%',
        right: '16%',
        nodeWidth: 16,
        nodeGap: 12,
        nodeAlign: 'justify',
        draggable: false,
        layoutIterations: 12,
        emphasis: {
          focus: 'trajectory',
          lineStyle: { opacity: 0.68 },
          label: { fontWeight: 800 },
        },
        blur: {
          itemStyle: { opacity: 0.22 },
          lineStyle: { opacity: 0.06 },
          label: { opacity: 0.34 },
        },
        lineStyle: { curveness: 0.48 },
        label: { color: colors.text, fontSize: 11 },
      }],
    };

}
