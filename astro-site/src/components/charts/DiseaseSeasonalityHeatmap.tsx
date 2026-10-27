import { useEffect, useMemo, useRef, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echartsHeatmap';
import type { DiseaseDatasetMonthlyData } from './diseaseDataset';

type Language = 'en' | 'zh' | 'fr';
const labels = {
  en: { cases: 'Cases', deaths: 'Deaths', png: 'Download PNG', csv: 'Download CSV', allYears: 'Show all years', recentYears: 'Show recent years', low: 'Lower', high: 'Higher', empty: 'No monthly observations are available.', note: 'Each cell belongs to the selected country’s published monthly series. Blank cells mean unavailable data, not zero.' },
  zh: { cases: '病例', deaths: '死亡', png: '下载 PNG', csv: '下载 CSV', allYears: '显示全部年份', recentYears: '仅显示近 10 年', low: '低', high: '高', empty: '暂无月度监测记录。', note: '每个单元格来自所选国家发布的月度序列。空白代表数据缺失，并非零。' },
  fr: { cases: 'Cas', deaths: 'Décès', png: 'Télécharger PNG', csv: 'Télécharger CSV', allYears: 'Afficher toutes les années', recentYears: 'Afficher les années récentes', low: 'Faible', high: 'Élevé', empty: 'Aucune observation mensuelle.', note: 'Chaque cellule appartient à la série mensuelle publiée par le pays choisi. Une cellule vide indique une donnée absente, et non zéro.' },
};

export default function DiseaseSeasonalityHeatmap({ data, language = 'en', filename = 'disease-seasonality' }: { data: DiseaseDatasetMonthlyData; language?: Language; filename?: string }) {
  const [ready, setReady] = useState(false);
  const [dark, setDark] = useState(false);
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
  const [showAllYears, setShowAllYears] = useState(false);
  const [compact, setCompact] = useState(false);
  const chart = useRef<any>(null);
  const t = labels[language];
  useEffect(() => {
    setReady(true);
    const update = () => setDark(document.documentElement.getAttribute('data-theme') !== 'light');
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const resize = () => { setCompact(window.innerWidth < 640); chart.current?.getEchartsInstance?.()?.resize(); };
    resize();
    window.addEventListener('resize', resize);
    return () => { observer.disconnect(); window.removeEventListener('resize', resize); };
  }, []);
  const allYears = useMemo(() => [...new Set(data.months.map(month => month.slice(0, 4)))].sort(), [data.months]);
  const years = showAllYears ? allYears : allYears.slice(-10);
  const cells = useMemo(() => data.months.flatMap((month, index) => {
    const value = data[metric][index];
    const incompatible = data.coverage?.[metric === 'cases' ? 'cases_incompatible_months' : 'deaths_incompatible_months']?.[index];
    if (value == null || incompatible) return [];
    const yearIndex = years.indexOf(month.slice(0, 4));
    const monthIndex = Number(month.slice(5, 7)) - 1;
    return yearIndex < 0 || monthIndex < 0 || monthIndex > 11 ? [] : [[monthIndex, yearIndex, value]];
  }), [data, metric, years]);
  const max = Math.max(1, ...cells.map(cell => cell[2]));
  const months = language === 'zh' ? Array.from({ length: 12 }, (_, index) => `${index + 1}月`) : language === 'fr'
    ? ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.']
    : ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const option = useMemo(() => ({
    animation: false,
    grid: { left: 55, right: 20, top: 12, bottom: 42 },
    tooltip: { position: 'top', backgroundColor: dark ? '#17283a' : '#fffdfa', borderColor: dark ? '#51667f' : '#c9c2b8', textStyle: { color: dark ? '#f3f6fb' : '#162232' }, formatter: (params: { value: number[] }) => `${years[params.value[1]]}-${String(params.value[0] + 1).padStart(2, '0')}<br/>${t[metric]}: ${params.value[2].toLocaleString(language)}` },
    xAxis: { type: 'category', data: months, axisLabel: { color: dark ? '#adbdc9' : '#526271', interval: 0, formatter: (_value: string, index: number) => compact && index % 2 ? '' : months[index] } },
    yAxis: { type: 'category', data: years, axisLabel: { color: dark ? '#adbdc9' : '#526271' } },
    visualMap: { show: false, min: 0, max, inRange: { color: dark ? ['#1e3544', '#147d76', '#5eead4'] : ['#e9f4ef', '#75c8b6', '#0f766e'] } },
    series: [{ type: 'heatmap', data: cells, progressive: 1000, emphasis: { itemStyle: { borderColor: dark ? '#fff' : '#17283a', borderWidth: 1 } } }],
  }), [cells, compact, dark, language, max, months, metric, t, years]);
  const downloadCsv = () => {
    const csv = ['month,cases,deaths', ...data.months.map((month, index) => `${month},${data.cases[index] ?? ''},${data.deaths[index] ?? ''}`)].join('\r\n');
    const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `${filename}.csv`; link.click(); URL.revokeObjectURL(url);
  };
  const downloadPng = () => {
    const url = chart.current?.getEchartsInstance()?.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: dark ? '#142839' : '#fffdfa' });
    if (!url) return;
    const link = document.createElement('a'); link.href = url; link.download = `${filename}.png`; link.click();
  };
  return <div className="disease-seasonality">
    <div className="disease-trend-heading"><p className="disease-trend-note">{t.note}</p><div className="disease-trend-actions"><button type="button" onClick={downloadPng} disabled={!ready || !cells.length}>{t.png}</button><button type="button" onClick={downloadCsv}>{t.csv}</button></div></div>
    <div className="disease-trend-controls">{(['cases', 'deaths'] as const).filter(item => item === 'cases' || data.deaths.some(value => value != null)).map(item => <button type="button" key={item} aria-pressed={metric === item} onClick={() => setMetric(item)}>{t[item]}</button>)}{allYears.length > 10 && <button type="button" aria-pressed={showAllYears} onClick={() => setShowAllYears(value => !value)}>{showAllYears ? t.recentYears : t.allYears}</button>}</div>
    {cells.length ? ready ? <EChartsReact ref={chart} echarts={echarts} option={option} notMerge style={{ height: Math.max(280, years.length * 27 + 80), width: '100%' }} /> : <div className="disease-seasonality-fallback"><table aria-label={t.cases}><thead><tr><th></th>{months.map(month => <th key={month}>{month}</th>)}</tr></thead><tbody>{years.map((year, yearIndex) => <tr key={year}><th>{year}</th>{months.map((month, monthIndex) => { const value = cells.find(cell => cell[0] === monthIndex && cell[1] === yearIndex)?.[2]; return <td key={month} style={{ background: value == null ? 'transparent' : `rgba(15,118,110,${.12 + .78 * value / max})` }} title={value == null ? '—' : `${year}-${String(monthIndex + 1).padStart(2, '0')}: ${value.toLocaleString(language)}`}>{value == null ? '—' : <span className="sr-only">{value.toLocaleString(language)}</span>}</td>; })}</tr>)}</tbody></table></div> : <p className="disease-trend-empty">{t.empty}</p>}
    {cells.length > 0 && <div className="disease-seasonality-scale"><span>{t.low} · 0</span><i aria-hidden="true" /><span>{t.high} · {max.toLocaleString(language)}</span></div>}
  </div>;
}
