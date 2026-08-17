import { useMemo, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echartsResearch';
import ChartFrame from '../charts/ChartFrame';
import { useChartLanguage, useChartTheme } from '../charts/chartPreferences';
import type { EChartsOption } from 'echarts';

type HotspotPeriod = {
  period: string;
  label?: string;
  start_date?: string;
  article_count?: number;
  topic_mentions?: number;
};

type StreamPoint = {
  period: string;
  count: number;
  share_of_articles?: number;
};

type StreamSeries = {
  topic: string;
  slug: string;
  count_total: number;
  points: StreamPoint[];
};

type HeatmapCell = {
  period: string;
  count: number;
  intensity?: number;
};

type HeatmapRow = {
  key: string;
  disease_name_en: string;
  disease_name_zh?: string | null;
  topic: string;
  count_total: number;
  cells: HeatmapCell[];
};

type BurstArticle = {
  slug?: string | null;
  title?: string | null;
  journal?: string | null;
  published_at?: string | null;
};

type Burst = {
  period: string;
  label?: string;
  topic: string;
  count: number;
  previous_count?: number;
  growth: number;
  share?: number;
  share_delta?: number;
  burst_score: number;
  articles?: BurstArticle[];
};

type AlluvialPeriod = {
  period: string;
  label?: string;
  start_date?: string;
  end_date?: string;
};

type AlluvialTopic = {
  topic: string;
  slug: string;
};

type AlluvialNode = {
  period: string;
  topic: string;
  slug: string;
  count: number;
  rank?: number;
  y0?: number;
  y1?: number;
};

type AlluvialLink = {
  topic: string;
  slug: string;
  source_period: string;
  target_period: string;
  source_count: number;
  target_count: number;
  value: number;
};

type Hotspots = {
  streamgraph?: {
    periods?: HotspotPeriod[];
    series?: StreamSeries[];
  };
  heatmap?: {
    periods?: HotspotPeriod[];
    rows?: HeatmapRow[];
  };
  burst_timeline?: {
    bursts?: Burst[];
  };
  alluvial?: {
    periods?: AlluvialPeriod[];
    topics?: AlluvialTopic[];
    nodes?: AlluvialNode[];
    links?: AlluvialLink[];
  };
  interpretation_note?: {
    en?: string;
    zh?: string;
  };
};

type ChartKey = 'stream' | 'heatmap' | 'burst' | 'migration';

interface Props {
  hotspots: Hotspots;
}

function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function percentPoints(value: number | undefined) {
  const points = Math.round((value ?? 0) * 100);
  return `${points >= 0 ? '+' : ''}${points} pp`;
}

function periodDate(period: HotspotPeriod) {
  return period.start_date ?? `${period.period}-01`;
}

function periodShort(value: string) {
  return value.replace(/^(\d{4})-Q(\d)$/, 'Q$2 $1').replace(/^(\d{4})-(\d{2})$/, '$1-$2');
}

function periodDisplay(value: string, periods: AlluvialPeriod[]) {
  const period = periods.find((item) => item.period === value);
  return period?.label ?? periodShort(value);
}

export default function ResearchHotspotCharts({ hotspots }: Props) {
  const lang = useChartLanguage();
  const theme = useChartTheme();
  const [active, setActive] = useState<ChartKey>('stream');
  const [isChartReady, setIsChartReady] = useState(false);

  const t = (en: string, zh: string) => (lang === 'zh' ? zh : en);
  const streamPeriods = hotspots.streamgraph?.periods ?? [];
  const streamSeries = hotspots.streamgraph?.series ?? [];
  const heatmapPeriods = hotspots.heatmap?.periods ?? [];
  const heatmapRows = hotspots.heatmap?.rows ?? [];
  const bursts = hotspots.burst_timeline?.bursts ?? [];
  const alluvialPeriods = hotspots.alluvial?.periods ?? [];
  const alluvialTopics = hotspots.alluvial?.topics ?? [];
  const alluvialNodes = hotspots.alluvial?.nodes ?? [];
  const alluvialLinks = hotspots.alluvial?.links ?? [];
  const alluvialPeriodKeys = useMemo(() => (
    alluvialPeriods.length
      ? alluvialPeriods.map((period) => period.period)
      : Array.from(new Set([
        ...alluvialNodes.map((node) => node.period),
        ...alluvialLinks.flatMap((link) => [link.source_period, link.target_period]),
      ])).sort()
  ), [alluvialLinks, alluvialNodes, alluvialPeriods]);

  const colors = useMemo(() => {
    const dark = theme === 'dark';
    return {
      text: dark ? '#f3f6fb' : '#162232',
      muted: dark ? '#a4b1c1' : '#556070',
      faint: dark ? '#7e91a6' : '#7a8ea0',
      line: dark ? '#344a64' : '#d9d2c7',
      surface: dark ? '#122132' : '#f7f3ec',
      popover: dark ? '#17283a' : '#fffdfa',
      palette: dark
        ? ['#76b7b2', '#e09976', '#7aa6c4', '#f0c16a', '#a48cf2', '#75c6ee', '#ef7878', '#9ed661']
        : ['#2a8f87', '#e06e4e', '#215f7c', '#d99121', '#7c5fd6', '#0e88b9', '#ca4b4b', '#70991e'],
    };
  }, [theme]);

  const streamOption = useMemo<EChartsOption>(() => {
    const pointLookup = new Map(streamSeries.flatMap((series) => (
      (series.points ?? []).map((point) => [`${series.topic}::${point.period}`, point] as const)
    )));
    const data: [string, number, string][] = streamPeriods.flatMap((period) => streamSeries.map((series) => {
      const point = pointLookup.get(`${series.topic}::${period.period}`);
      return [periodDate(period), point?.count ?? 0, series.topic] as [string, number, string];
    }));
    return {
      backgroundColor: 'transparent',
      color: colors.palette,
      aria: {
        enabled: true,
        decal: { show: false },
        label: { description: t('Monthly theme river showing Research Radar topic attention.', '展示 Research Radar 主题关注度月度变化的主题河流图。') },
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
          if (!sorted.length) return t('No topic mentions in this month.', '该月没有主题记录。');
          return `<strong>${escapeHtml(rows[0]?.axisValueLabel ?? rows[0]?.name)}</strong><br/>${sorted.map((item) => `${item.marker}${escapeHtml(item.topic)}: ${item.count}`).join('<br/>')}`;
        },
      },
      legend: {
        type: 'scroll',
        bottom: 0,
        textStyle: { color: colors.muted },
        pageTextStyle: { color: colors.muted },
        data: streamSeries.map((series) => series.topic),
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
  }, [colors, lang, streamPeriods, streamSeries]);

  const heatmapOption = useMemo<EChartsOption>(() => {
    const xLabels = heatmapPeriods.map((period) => period.label ?? period.period);
    const yLabels = heatmapRows.map((row) => `${lang === 'zh' ? (row.disease_name_zh || row.disease_name_en) : row.disease_name_en} · ${row.topic}`);
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
        label: { description: t('Heatmap of high-confidence disease and topic combinations over time.', '按时间展示高置信疾病与主题组合的热力矩阵。') },
      },
      tooltip: {
        confine: true,
        backgroundColor: colors.popover,
        borderColor: colors.line,
        textStyle: { color: colors.text },
        formatter: (param: any) => {
          const value = param?.data ?? [];
          const row = heatmapRows[value[1]] as HeatmapRow | undefined;
          return `<strong>${escapeHtml(row ? `${lang === 'zh' ? (row.disease_name_zh || row.disease_name_en) : row.disease_name_en} · ${row.topic}` : '')}</strong><br/>${escapeHtml(xLabels[value[0]] ?? '')}: ${value[2] ?? 0} ${t('papers', '篇论文')}`;
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
  }, [colors, heatmapPeriods, heatmapRows, lang, theme]);

  const burstOption = useMemo<EChartsOption>(() => {
    const yLabels = bursts.map((burst) => `${burst.label ?? burst.period} · ${burst.topic}`);
    return {
      backgroundColor: 'transparent',
      color: colors.palette,
      aria: {
        enabled: true,
        label: { description: t('Ranked monthly topic burst events.', '按月度主题升温事件排序的图表。') },
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
          return `<strong>${escapeHtml(burst.topic)}</strong><br/>${escapeHtml(burst.label ?? burst.period)} · ${burst.count} ${t('papers', '篇论文')}<br/>${t('Growth', '增长')}: ${burst.growth >= 0 ? '+' : ''}${burst.growth} · ${t('Share move', '占比变化')}: ${percentPoints(burst.share_delta)}<br/>${t('Burst score', '爆发分')}: ${burst.burst_score}`;
        },
      },
      grid: { top: 22, right: 112, bottom: 48, left: 230 },
      xAxis: {
        type: 'value',
        name: t('Burst score', '爆发分'),
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
  }, [bursts, colors, lang]);

  const migrationOption = useMemo<EChartsOption>(() => {
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
        period_label: periodDisplay(node.period, alluvialPeriods),
        topic: node.topic,
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
          formatter: node.topic,
        },
      };
    });
    const links = alluvialLinks
      .map((link) => ({
        source: `${link.source_period}::${link.slug}`,
        target: `${link.target_period}::${link.slug}`,
        value: Math.max(0.1, link.value || link.source_count || link.target_count || 0.1),
        topic: link.topic,
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
        label: { description: t('Quarterly Sankey view of topic attention flow.', '按季度展示主题关注度流向的桑基图。') },
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
            return `<strong>${escapeHtml(data.topic)}</strong><br/>${escapeHtml(periodDisplay(data.source_period, alluvialPeriods))} → ${escapeHtml(periodDisplay(data.target_period, alluvialPeriods))}<br/>${data.source_count ?? 0} → ${data.target_count ?? 0} ${t('mentions', '次提及')}`;
          }
          return `<strong>${escapeHtml(data.topic)}</strong><br/>${escapeHtml(data.period_label ?? periodDisplay(data.period, alluvialPeriods))} · ${data.count ?? 0} ${t('mentions', '次提及')}`;
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
  }, [alluvialLinks, alluvialNodes, alluvialPeriodKeys, alluvialPeriods, alluvialTopics, colors, lang, theme]);

  const activeOption = {
    stream: streamOption,
    heatmap: heatmapOption,
    burst: burstOption,
    migration: migrationOption,
  }[active];

  const activeHeight = active === 'migration' ? 520 : active === 'heatmap' ? 460 : 420;
  const chartTabs: Array<{ key: ChartKey; labelEn: string; labelZh: string; hintEn: string; hintZh: string; count: number }> = [
    { key: 'stream', labelEn: 'Topic Streamgraph', labelZh: '主题河流图', hintEn: 'Which topics expand or fade month by month', hintZh: '看主题逐月升温或退潮', count: streamSeries.length },
    { key: 'burst', labelEn: 'Burst Timeline', labelZh: '升温时间线', hintEn: 'What suddenly rose above the recent baseline', hintZh: '看哪些主题突然上升', count: bursts.length },
    { key: 'heatmap', labelEn: 'Disease-topic Heatmap', labelZh: '疾病—主题热力图', hintEn: 'Where topic attention concentrates by disease', hintZh: '看疾病下的主题关注集中点', count: heatmapRows.length },
    { key: 'migration', labelEn: 'Alluvial Flow', labelZh: '冲积流图', hintEn: 'How topic attention persists across quarters', hintZh: '看季度间主题关注延续', count: alluvialLinks.length },
  ];
  const hasData = streamSeries.length || heatmapRows.length || bursts.length || alluvialNodes.length;

  const renderTable = () => {
    if (active === 'stream') {
      return (
        <table className="data-preview-table">
          <thead>
            <tr>
              <th className="is-sticky">{t('Month', '月份')}</th>
              {streamSeries.map((series) => <th key={series.slug}>{series.topic}</th>)}
            </tr>
          </thead>
          <tbody>
            {streamPeriods.map((period) => (
              <tr key={period.period}>
                <td className="is-sticky">{period.label ?? period.period}</td>
                {streamSeries.map((series) => (
                  <td key={series.slug}>{series.points.find((point) => point.period === period.period)?.count ?? 0}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }

    if (active === 'burst') {
      return (
        <table className="data-preview-table">
          <thead>
            <tr>
              <th className="is-sticky">{t('Month', '月份')}</th>
              <th>{t('Topic', '主题')}</th>
              <th>{t('Papers', '论文')}</th>
              <th>{t('Change', '变化')}</th>
              <th>{t('Burst score', '升温分')}</th>
            </tr>
          </thead>
          <tbody>
            {bursts.map((burst) => (
              <tr key={`${burst.period}::${burst.topic}`}>
                <td className="is-sticky">{burst.label ?? burst.period}</td>
                <td>{burst.topic}</td>
                <td>{burst.count}</td>
                <td>{burst.growth >= 0 ? '+' : ''}{burst.growth}</td>
                <td>{burst.burst_score}</td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    }

    if (active === 'heatmap') {
      return (
        <table className="data-preview-table">
          <thead>
            <tr>
              <th className="is-sticky">{t('Disease · topic', '疾病 · 主题')}</th>
              {heatmapPeriods.map((period) => <th key={period.period}>{period.label ?? period.period}</th>)}
            </tr>
          </thead>
          <tbody>
            {heatmapRows.map((row) => (
              <tr key={row.key}>
                <td className="is-sticky">{lang === 'zh' ? (row.disease_name_zh || row.disease_name_en) : row.disease_name_en} · {row.topic}</td>
                {heatmapPeriods.map((period) => (
                  <td key={period.period}>{row.cells.find((cell) => cell.period === period.period)?.count ?? 0}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      );
    }

    return (
      <table className="data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">{t('Topic', '主题')}</th>
            <th>{t('From', '起始')}</th>
            <th>{t('To', '终止')}</th>
            <th>{t('Source', '起始值')}</th>
            <th>{t('Target', '终止值')}</th>
          </tr>
        </thead>
        <tbody>
          {alluvialLinks.map((link) => (
            <tr key={`${link.slug}::${link.source_period}::${link.target_period}`}>
              <td className="is-sticky">{link.topic}</td>
              <td>{periodShort(link.source_period)}</td>
              <td>{periodShort(link.target_period)}</td>
              <td>{link.source_count}</td>
              <td>{link.target_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  const toolbar = (
    <div className="research-chart-switcher" role="group" aria-label={t('Research trend visualization', '研究趋势可视化')}>
      {chartTabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          className={`chart-toggle ${active === tab.key ? 'chart-toggle-active' : ''}`}
          aria-pressed={active === tab.key}
          title={`${t(tab.hintEn, tab.hintZh)} · ${tab.count}`}
          onClick={() => {
            if (active === tab.key) return;
            setIsChartReady(false);
            setActive(tab.key);
          }}
        >
          {t(tab.labelEn, tab.labelZh)}
        </button>
      ))}
    </div>
  );

  if (!hasData) {
    return <p className="research-hotspots-empty">{t('No trend data available.', '暂无趋势数据。')}</p>;
  }

  return (
    <div className="research-hotspots-widget">
      <style>{`
        .research-hotspots-widget { margin-top: .75rem; }
        .research-hotspot-interpretation { margin-bottom: .75rem; border-left: 2px solid rgb(var(--color-accent) / .75); background: rgb(var(--color-accent) / .045); padding: .65rem .8rem; color: rgb(var(--text-muted)); font-size: .72rem; line-height: 1.55; }
        .research-hotspots-widget .chart-canvas { overflow-x: auto; overflow-y: hidden; }
        .research-chart-switcher { display: flex; flex-wrap: wrap; align-items: center; }
        .research-chart-switcher .chart-toggle + .chart-toggle { border-left: 0; }
        .research-hotspot-plot { position: relative; width: 100%; min-height: 0; overflow: hidden; }
        .research-flow-axis { position: absolute; z-index: 1; left: 16%; right: 16%; top: .45rem; height: 2.25rem; pointer-events: none; }
        .research-flow-axis span { position: absolute; top: 0; display: grid; justify-items: center; min-width: 4.8rem; color: rgb(var(--text-muted)); font: 700 .72rem/1.1 'Source Sans 3', sans-serif; }
        .research-flow-axis i { display: block; width: 1px; height: .62rem; margin-bottom: .32rem; background: rgb(var(--border) / .82); }
        .research-flow-axis b { font-weight: 700; white-space: nowrap; }
        .research-hotspot-loading { position: absolute; z-index: 2; inset: 1rem; display: grid; place-items: center; background: rgb(var(--surface) / .78); color: rgb(var(--text-muted)); font-size: .75rem; pointer-events: none; }
        .research-hotspots-empty { margin-top: .75rem; color: rgb(var(--text-muted)); font-size: .78rem; }
        @media (max-width: 720px) {
          .research-chart-switcher { display: grid; grid-template-columns: 1fr 1fr; width: 100%; }
          .research-chart-switcher .chart-toggle { min-width: 0; }
          .research-chart-switcher .chart-toggle:nth-child(3) { border-left: 1px solid rgb(var(--border) / .82); }
          .research-chart-switcher .chart-toggle:nth-child(n+3) { border-top: 0; }
          .research-hotspots-widget .chart-canvas { -webkit-overflow-scrolling: touch; }
          .research-hotspot-plot { min-width: 44rem; }
          .research-hotspot-plot-burst { min-width: 46rem; }
          .research-hotspot-plot-heatmap,
          .research-hotspot-plot-migration { min-width: 58rem; }
        }
      `}</style>
      <p className="research-hotspot-interpretation">
        {t(
          hotspots.interpretation_note?.en || 'Hotspots show Research Radar literature attention, not disease risk or incidence.',
          hotspots.interpretation_note?.zh || '热点图展示的是 Research Radar 文献关注度，不代表疾病风险或发病水平。',
        )}
      </p>
      <ChartFrame
        lang={lang}
        toolbar={toolbar}
        chart={({ isFullscreen }) => (
          <div className={`research-hotspot-plot research-hotspot-plot-${active}`} style={{ height: isFullscreen ? '100%' : activeHeight }}>
            {active === 'migration' && alluvialPeriodKeys.length > 1 && (
              <div className="research-flow-axis" aria-hidden="true">
                {alluvialPeriodKeys.map((period, index) => {
                  const lastIndex = alluvialPeriodKeys.length - 1;
                  const left = `${(index / Math.max(1, lastIndex)) * 100}%`;
                  const transform = index === 0 ? 'translateX(0)' : index === lastIndex ? 'translateX(-100%)' : 'translateX(-50%)';
                  const textAlign = index === 0 ? 'left' : index === lastIndex ? 'right' : 'center';
                  return (
                    <span key={period} style={{ left, transform, textAlign }}>
                      <i />
                      <b>{periodDisplay(period, alluvialPeriods)}</b>
                    </span>
                  );
                })}
              </div>
            )}
            {!isChartReady && <div className="research-hotspot-loading" role="status">{t('Loading chart…', '正在加载图表…')}</div>}
            <EChartsReact
              key={active}
              echarts={echarts}
              option={activeOption}
              notMerge
              lazyUpdate
              onChartReady={() => setIsChartReady(true)}
              style={{ width: '100%', height: '100%' }}
            />
          </div>
        )}
        table={renderTable}
        stageHeight={activeHeight}
      />
    </div>
  );
}
