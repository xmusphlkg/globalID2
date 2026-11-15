import { hotspotLabels, hotspotColors } from './hotspots/presentation.ts';
import { buildStreamOption } from './hotspots/stream-option.ts';
import { buildHeatmapOption } from './hotspots/heatmap-option.ts';
import { buildBurstOption } from './hotspots/burst-option.ts';
import { buildMigrationOption } from './hotspots/migration-option.ts';
import { type Hotspots, type ChartKey } from './hotspots/types.ts';
import { periodDisplay } from './hotspots/presentation.ts';
import GIDSLoader from '../GIDSLoader';
import { useEffect, useMemo, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echartsResearch';
import ChartFrame from '../charts/ChartFrame';
import { useChartLanguage, useChartTheme } from '../charts/chartPreferences';
import type { EChartsOption } from 'echarts';

interface Props {
  hotspots: Hotspots;
  initialLanguage?: 'en' | 'zh' | 'fr';
}

export default function ResearchHotspotCharts({ hotspots, initialLanguage = 'en' }: Props) {
  const lang = useChartLanguage(initialLanguage);
  const theme = useChartTheme();
  const [active, setActive] = useState<ChartKey>('stream');
  useEffect(() => {
    const value = new URLSearchParams(window.location.search).get('research_chart');
    if (value === 'heatmap' || value === 'burst' || value === 'migration' || value === 'stream') setActive(value);
  }, []);
  const [isChartReady, setIsChartReady] = useState(false);

  const { t, topicLabel, diseaseLabel, periodLabel } = hotspotLabels(lang);
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

  const colors = useMemo(() => hotspotColors(theme), [theme]);

  const streamOption = useMemo<EChartsOption>(() => buildStreamOption({ colors, lang, streamPeriods, streamSeries }), [colors, lang, streamPeriods, streamSeries]);

  const heatmapOption = useMemo<EChartsOption>(() => buildHeatmapOption({ colors, heatmapPeriods, heatmapRows, lang, theme }), [colors, heatmapPeriods, heatmapRows, lang, theme]);

  const burstOption = useMemo<EChartsOption>(() => buildBurstOption({ bursts, colors, lang }), [bursts, colors, lang]);

  const migrationOption = useMemo<EChartsOption>(() => buildMigrationOption({ alluvialLinks, alluvialNodes, alluvialPeriodKeys, alluvialPeriods, alluvialTopics, colors, lang, theme }), [alluvialLinks, alluvialNodes, alluvialPeriodKeys, alluvialPeriods, alluvialTopics, colors, lang, theme]);

  const activeOption = {
    stream: streamOption,
    heatmap: heatmapOption,
    burst: burstOption,
    migration: migrationOption,
  }[active];

  const activeHeight = active === 'migration' ? 520 : active === 'heatmap' ? 460 : 420;
  const chartTabs: Array<{ key: ChartKey; labelEn: string; labelZh: string; labelFr: string; hintEn: string; hintZh: string; hintFr: string; count: number }> = [
    { key: 'stream', labelEn: 'Topic Streamgraph', labelZh: '主题河流图', labelFr: 'Rivière thématique', hintEn: 'Which topics expand or fade month by month', hintZh: '看主题逐月升温或退潮', hintFr: 'Thèmes qui progressent ou reculent chaque mois', count: streamSeries.length },
    { key: 'burst', labelEn: 'Burst Timeline', labelZh: '升温时间线', labelFr: 'Chronologie des accélérations', hintEn: 'What suddenly rose above the recent baseline', hintZh: '看哪些主题突然上升', hintFr: 'Thèmes soudainement supérieurs à la référence récente', count: bursts.length },
    { key: 'heatmap', labelEn: 'Disease-topic Heatmap', labelZh: '疾病—主题热力图', labelFr: 'Carte maladie-thème', hintEn: 'Where topic attention concentrates by disease', hintZh: '看疾病下的主题关注集中点', hintFr: 'Concentration des thèmes par maladie', count: heatmapRows.length },
    { key: 'migration', labelEn: 'Alluvial Flow', labelZh: '冲积流图', labelFr: 'Flux alluvial', hintEn: 'How topic attention persists across quarters', hintZh: '看季度间主题关注延续', hintFr: 'Persistance de l’attention entre les trimestres', count: alluvialLinks.length },
  ];
  const hasData = streamSeries.length || heatmapRows.length || bursts.length || alluvialNodes.length;

  const renderTable = () => {
    if (active === 'stream') {
      return (
        <table className="data-preview-table">
          <thead>
            <tr>
              <th className="is-sticky">{t('Month', '月份', 'Mois')}</th>
              {streamSeries.map((series) => <th key={series.slug}>{topicLabel(series.topic)}</th>)}
            </tr>
          </thead>
          <tbody>
            {streamPeriods.map((period) => (
              <tr key={period.period}>
                <td className="is-sticky">{periodLabel(period)}</td>
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
              <th className="is-sticky">{t('Month', '月份', 'Mois')}</th>
              <th>{t('Topic', '主题', 'Thème')}</th>
              <th>{t('Papers', '论文', 'Publications')}</th>
              <th>{t('Change', '变化', 'Variation')}</th>
              <th>{t('Burst score', '升温分', 'Score d’accélération')}</th>
            </tr>
          </thead>
          <tbody>
            {bursts.map((burst) => (
              <tr key={`${burst.period}::${burst.topic}`}>
                <td className="is-sticky">{periodLabel(burst)}</td>
                <td>{topicLabel(burst.topic)}</td>
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
              <th className="is-sticky">{t('Disease · topic', '疾病 · 主题', 'Maladie · thème')}</th>
              {heatmapPeriods.map((period) => <th key={period.period}>{periodLabel(period)}</th>)}
            </tr>
          </thead>
          <tbody>
            {heatmapRows.map((row) => (
              <tr key={row.key}>
                <td className="is-sticky">{diseaseLabel(row)} · {topicLabel(row.topic)}</td>
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
            <th className="is-sticky">{t('Topic', '主题', 'Thème')}</th>
            <th>{t('From', '起始', 'De')}</th>
            <th>{t('To', '终止', 'À')}</th>
            <th>{t('Source', '起始值', 'Valeur initiale')}</th>
            <th>{t('Target', '终止值', 'Valeur finale')}</th>
          </tr>
        </thead>
        <tbody>
          {alluvialLinks.map((link) => (
            <tr key={`${link.slug}::${link.source_period}::${link.target_period}`}>
              <td className="is-sticky">{topicLabel(link.topic)}</td>
              <td>{periodLabel({ period: link.source_period })}</td>
              <td>{periodLabel({ period: link.target_period })}</td>
              <td>{link.source_count}</td>
              <td>{link.target_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };

  const toolbar = (
    <div className="research-chart-switcher" role="group" aria-label={t('Research trend visualization', '研究趋势可视化', 'Visualisation des tendances de recherche')}>
      {chartTabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          className={`chart-toggle ${active === tab.key ? 'chart-toggle-active' : ''}`}
          aria-pressed={active === tab.key}
          title={`${t(tab.hintEn, tab.hintZh, tab.hintFr)} · ${tab.count}`}
          onClick={() => {
            if (active === tab.key) return;
            setIsChartReady(false);
            setActive(tab.key);
          }}
        >
          {t(tab.labelEn, tab.labelZh, tab.labelFr)}
        </button>
      ))}
    </div>
  );

  if (!hasData) {
    return <p className="research-hotspots-empty">{t('No trend data available.', '暂无趋势数据。', 'Aucune donnée de tendance disponible.')}</p>;
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
        .research-flow-axis span { position: absolute; top: 0; display: grid; justify-items: center; min-width: 4.8rem; color: rgb(var(--text-muted)); font: 700 .72rem/1.1 'IBM Plex Sans', sans-serif; }
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
          hotspots.interpretation_note?.fr || 'Les points chauds montrent l’attention bibliographique du Radar de recherche, et non le risque ou l’incidence des maladies.',
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
                      <b>{periodLabel({ period, label: periodDisplay(period, alluvialPeriods), start_date: alluvialPeriods.find((item) => item.period === period)?.start_date })}</b>
                    </span>
                  );
                })}
              </div>
            )}
            {!isChartReady && <div className="research-hotspot-loading" role="status"><GIDSLoader language={lang} label={t('Loading chart…', '正在绘制文献趋势…', 'Chargement du graphique…')} /></div>}
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
        pngFilename={`gids-research-${active}.png`}
        showData={false}
        onShare={() => {
          const url = new URL(window.location.href);
          url.searchParams.set('research_chart', active);
          return url.toString();
        }}
      />
    </div>
  );
}
