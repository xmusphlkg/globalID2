import { useId, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react';
import EChartsReact from '../../../lib/echartsReact';
import echarts from '../../../lib/echartsSurveillance';
import { axisColors, regionColor } from '../surveillance/presentation/seriesColors';
import { chartSurfaceColor } from '../chartExport';
import { trendFor, type DiseaseDataset, type Language, type MapWindow, type PopulationDataset, type Region } from './model';

const copy = {
  en: { add: 'Choose regions', search: 'Search by name or code', done: 'Done', none: 'No matching regions', region: 'Region', highlight: 'Highlight', remove: 'Remove', choose: 'Select a region to see its trend.', empty: 'No reported values for these regions and dates.' },
  zh: { add: '选择地区', search: '搜索地区名称或编码', done: '完成', none: '没有匹配的地区', region: '地区', highlight: '突出显示', remove: '移除', choose: '请选择地区以查看趋势。', empty: '所选地区在此期间没有可展示的数值。' },
  fr: { add: 'Choisir les régions', search: 'Rechercher un nom ou un code', done: 'Terminé', none: 'Aucune région correspondante', region: 'Région', highlight: 'Mettre en évidence', remove: 'Retirer', choose: 'Sélectionnez une région pour afficher sa tendance.', empty: 'Aucune valeur pour les régions et les dates sélectionnées.' },
};

export interface RegionalTrendHandle { exportPng: () => string | undefined }
interface Props {
  ref?: Ref<RegionalTrendHandle>;
  regions: Region[];
  codes: string[];
  onChange: (codes: string[]) => void;
  dataset: DiseaseDataset;
  population: PopulationDataset;
  range: MapWindow;
  comparison: MapWindow | null;
  lang: Language;
  dark: boolean;
  fullscreen: boolean;
  metric: string;
  cadence: string;
  currentLabel: string;
  comparisonLabel: string;
}

type Points = ReturnType<typeof trendFor>;
function periodCaption(points: Points, index: number) {
  const point = points[index];
  return point ? `${point.from} – ${point.to}` : '—';
}

export default function RegionalTrend({ ref, regions, codes, onChange, dataset, population, range, comparison,
  lang, dark, fullscreen, metric, cadence, currentLabel, comparisonLabel }: Props) {
  const t = copy[lang], id = useId();
  const picker = useRef<HTMLDialogElement>(null);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState('');
  const options = useMemo(() => regions.map((region, index) => ({
    region, color: regionColor(index), name: lang === 'zh' ? region.name_zh || region.name_en : region.name_en,
  })), [regions, lang]);
  const selected = useMemo(() => options.filter(item => codes.includes(item.region.code)), [options, codes]);
  const groups = useMemo(() => selected.map(item => ({
    ...item, current: trendFor(item.region, dataset, population, range),
    past: comparison ? trendFor(item.region, dataset, population, comparison) : [],
  })), [selected, dataset, population, range, comparison]);
  const needle = query.trim().toLocaleLowerCase(lang);
  const matches = options.filter(({ region }) => [region.code, region.name_en, region.name_zh].some(
    value => value?.toLocaleLowerCase(lang).includes(needle)));
  const toggle = (code: string) => {
    if (highlight === code) setHighlight('');
    onChange(codes.includes(code) ? codes.filter(item => item !== code) : [...codes, code]);
  };

  const option = useMemo(() => {
    const { ink, grid } = axisColors(dark);
    const current = groups[0]?.current ?? [], past = groups[0]?.past ?? [];
    const format = new Intl.NumberFormat(lang, range.metric === 'cases' ? { maximumFractionDigits: 0 } : { maximumSignificantDigits: 4 });
    const hasValues = groups.some(group => [group.current, group.past].some(points => points.some(p => p.value !== null)));
    return {
      animation: false,
      graphic: hasValues ? [] : [{ type: 'text', left: 'center', top: 'middle',
        style: { text: t.empty, fill: ink, fontSize: 12, width: 220, overflow: 'break', align: 'center' } }],
      title: { text: `${metric} · ${cadence}`, left: 4, textStyle: { color: ink, fontSize: 12, fontWeight: 500 },
        subtext: comparison ? `━  ${currentLabel}     ┄  ${comparisonLabel}` : '', subtextStyle: { color: ink } },
      grid: { left: 12, right: 16, top: comparison ? 64 : 44, bottom: 12, containLabel: true },
      tooltip: { trigger: 'axis', confine: true, enterable: true, padding: 0, backgroundColor: chartSurfaceColor(), borderColor: grid,
        formatter: (items: Array<{ dataIndex: number }>) => {
          const index = items[0]?.dataIndex;
          if (index === undefined) return '';
          const wrap = document.createElement('div');
          wrap.className = 'regional-tooltip';
          const table = document.createElement('table');
          const header = table.createTHead().insertRow();
          for (const [title, dates] of [[t.region, ''], [currentLabel, periodCaption(current, index)],
            ...(comparison ? [[comparisonLabel, periodCaption(past, index)]] : [])]) {
            const th = document.createElement('th');
            th.scope = 'col';
            th.textContent = title;
            if (dates) {
              const small = document.createElement('small');
              small.textContent = dates;
              th.append(small);
            }
            header.append(th);
          }
          const body = table.createTBody();
          for (const group of groups) {
            const row = body.insertRow(), name = document.createElement('th');
            name.scope = 'row';
            name.textContent = group.name;
            name.style.borderInlineStart = `3px solid ${group.color}`;
            row.append(name);
            for (const points of comparison ? [group.current, group.past] : [group.current]) {
              const value = points[index]?.value;
              row.insertCell().textContent = value == null ? '—' : format.format(value);
            }
          }
          wrap.append(table);
          return wrap;
        } },
      xAxis: { type: 'category', boundaryGap: false, axisLabel: { color: ink, hideOverlap: true }, axisLine: { lineStyle: { color: grid } },
        data: Array.from({ length: Math.max(current.length, past.length) }, (_, i) => current[i]?.label ?? past[i].label) },
      yAxis: { type: 'value', axisLabel: { color: ink }, splitLine: { lineStyle: { color: grid } } },
      series: groups.flatMap(({ region, name, color, current, past }) =>
        (comparison ? [current, past] : [current]).map((points, i) => ({
          id: `${region.code}/${i ? 'comparison' : 'current'}`, name, type: 'line',
          data: points.map(point => point.value), connectNulls: false, symbolSize: 5, showSymbol: points.length <= 60,
          itemStyle: { color }, lineStyle: { color, width: 2, type: i ? 'dashed' : 'solid' },
        }))),
    };
  }, [groups, t, dark, lang, range.metric, metric, cadence, comparison, currentLabel, comparisonLabel]);
  const screenOption = useMemo(() => {
    if (!codes.includes(highlight)) return option;
    return { ...option, series: option.series.map(series => {
      const active = series.id.startsWith(highlight + '/'), opacity = active ? 1 : .23;
      return { ...series, z: active ? 5 : 2, itemStyle: { ...series.itemStyle, opacity },
        lineStyle: { ...series.lineStyle, opacity, width: active ? 3 : 2 } };
    }) };
  }, [option, codes, highlight]);

  // Render a separate export so every selected region is in the legend, even on a narrow screen.
  useImperativeHandle(ref, () => ({ exportPng() {
    if (!groups.length) return;
    const legendRows = Math.ceil(groups.length / 3);
    const exportChart = echarts.init(document.createElement('div'), undefined, { width: 1080, height: 420 + legendRows * 26 });
    try {
      exportChart.setOption({ ...option, backgroundColor: chartSurfaceColor(), grid: { ...option.grid, bottom: legendRows * 26 + 16 },
        legend: groups.map((group, index) => ({
          data: [group.name], left: 18 + (index % 3) * 350, top: 420 + Math.floor(index / 3) * 26,
          textStyle: { color: axisColors(dark).ink },
        })),
      });
      return exportChart.getDataURL({ type: 'png', pixelRatio: 2 });
    } finally { exportChart.dispose(); }
  } }), [groups, option, dark]);

  return <>
    <div className="surveillance-selected regional-trend-selection">
      <button type="button" aria-haspopup="dialog" onClick={() => { setQuery(''); picker.current?.showModal(); }}>{t.add}</button>
      {selected.map(({ region, name, color }) => <span className="regional-legend-chip" key={region.code}>
        <button type="button" aria-label={`${t.highlight} ${name}`} aria-pressed={highlight === region.code}
          onClick={() => setHighlight(highlight === region.code ? '' : region.code)}>
          <span className="surveillance-swatch" style={{ backgroundColor: color }} />{name}
        </button>
        <button type="button" aria-label={`${t.remove} ${name}`} onClick={() => toggle(region.code)}>×</button>
      </span>)}
    </div>
    <dialog ref={picker} className="surveillance-picker-dialog regional-trend-picker" aria-labelledby={id}>
      <header><strong id={id}>{t.add} ({codes.length})</strong><button type="button" onClick={() => picker.current?.close()}>{t.done}</button></header>
      <input autoFocus type="search" aria-label={t.search} placeholder={t.search} value={query} onChange={event => setQuery(event.target.value)} />
      <div className="regional-picker-list">{matches.map(({ region, name }) =>
        <label key={region.code} className="surveillance-entity-main">
          <input type="checkbox" checked={codes.includes(region.code)} onChange={() => toggle(region.code)} />
          {name}<small>{region.code}</small>
        </label>)}
        {!matches.length && <p role="status">{t.none}</p>}
      </div>
    </dialog>
    {codes.length ? <div className="subdivision-map-trend surveillance-plot" role="img" aria-label={`${metric} · ${selected.map(item => item.name).join(', ')}`}>
      <EChartsReact echarts={echarts} option={screenOption} notMerge style={{ height: fullscreen ? '55vh' : 360, minHeight: 280, width: '100%' }} />
    </div> : <p className="surveillance-empty" role="status">{t.choose}</p>}
  </>;
}
