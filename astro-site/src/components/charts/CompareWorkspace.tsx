import { useEffect, useMemo, useRef, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import { loadDiseaseDataset, type DiseaseDataset, type DiseaseDatasetSeriesEntry } from './diseaseDataset';

type DiseaseOption = { id: string; slug: string; en: string; zh: string; fr: string };
type Lang = 'en' | 'zh' | 'fr';
const t = {
  en: { diseases: 'Diseases (up to 2)', countries: 'Countries (2–4)', unit: 'Unit', count: 'Reported cases', rate: 'Per 100k, where published', share: 'Copy share link', png: 'Download PNG', csv: 'Download CSV', empty: 'Select a disease and at least two countries with published series.', missing: 'Rates appear only where a source publishes a usable population denominator.', note: 'Lines retain each source’s reporting period and case definition. Compare aligned dates and methods before interpreting differences.' },
  zh: { diseases: '疾病（最多 2 种）', countries: '国家（2–4 个）', unit: '单位', count: '报告病例数', rate: '每 10 万人（有可用分母时）', share: '复制分享链接', png: '下载 PNG', csv: '下载 CSV', empty: '请选择疾病及至少两个有公开序列的国家。', missing: '仅在来源提供可用人口分母时显示每 10 万人口数值。', note: '曲线保留各来源的报告周期和病例定义；解释差异前请核对日期与统计方法。' },
  fr: { diseases: 'Maladies (2 maximum)', countries: 'Pays (2 à 4)', unit: 'Unité', count: 'Cas déclarés', rate: 'Pour 100 000, si publié', share: 'Copier le lien', png: 'Télécharger PNG', csv: 'Télécharger CSV', empty: 'Choisissez une maladie et au moins deux pays avec des séries publiées.', missing: 'Le taux apparaît seulement lorsqu’un dénominateur valide est disponible.', note: 'Les courbes conservent la période et la définition de chaque source. Vérifiez les méthodes avant toute comparaison.' },
};
const palette = ['#0f766e', '#2563eb', '#c06127', '#9b4dcc'];

export default function CompareWorkspace({ diseases, language = 'en' }: { diseases: DiseaseOption[]; language?: Lang }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [unit, setUnit] = useState<'count' | 'rate'>('count');
  const [datasets, setDatasets] = useState<Record<string, DiseaseDataset>>({});
  const [loading, setLoading] = useState(false);
  const [copyState, setCopyState] = useState(false);
  const [dark, setDark] = useState(false);
  const [ready, setReady] = useState(false);
  const refs = useRef<Record<string, any>>({});
  const seededCountries = useRef(false);
  const urlCountries = useRef<string[]>([]);
  const label = t[language];
  const diseaseName = (id: string) => {
    const disease = diseases.find(item => item.id === id);
    if (!disease) return id;
    return language === 'zh' ? disease.zh : language === 'fr' ? disease.fr : disease.en;
  };
  const placeName = (entry: DiseaseDatasetSeriesEntry) => language === 'zh' ? entry.name_zh : language === 'fr' ? entry.name_fr || entry.name_en : entry.name_en;
  useEffect(() => {
    const update = () => setDark(document.documentElement.getAttribute('data-theme') !== 'light');
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const ids = params.get('d')?.split(',').filter(id => diseases.some(d => d.id === id)).slice(0, 2) ?? [];
    urlCountries.current = params.get('c')?.split(',').filter(code => /^[A-Z]{2}$/.test(code)).slice(0, 4) ?? [];
    setSelected(ids.length ? ids : diseases.slice(0, 1).map(d => d.id));
    setCountries(urlCountries.current);
    setUnit(params.get('u') === 'rate' ? 'rate' : 'count');
    setReady(true);
  }, [diseases]);
  useEffect(() => {
    if (!selected.length) return;
    let cancelled = false;
    setLoading(true);
    Promise.all(selected.map(async id => [id, await loadDiseaseDataset(`/site-data/diseases/${id.toLowerCase()}.json`)] as const))
      .then(entries => { if (!cancelled) { setDatasets(Object.fromEntries(entries)); setLoading(false); } })
      .catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);
  const available = useMemo(() => {
    const map = new Map<string, DiseaseDatasetSeriesEntry>();
    selected.forEach(id => Object.entries(datasets[id]?.country_series ?? {}).forEach(([code, entry]) => {
      if (/^[A-Z]{2}$/.test(code) && entry.dates?.length && !map.has(code)) map.set(code, entry);
    }));
    return [...map].sort((a, b) => a[1].name_en.localeCompare(b[1].name_en));
  }, [datasets, selected]);
  useEffect(() => {
    if (!ready || seededCountries.current || !available.length) return;
    seededCountries.current = true;
    if (!urlCountries.current.length) setCountries(available.slice(0, 2).map(([code]) => code));
  }, [available, ready]);
  const toggleDisease = (id: string) => setSelected(value => value.includes(id) ? value.filter(item => item !== id) : value.length < 2 ? [...value, id] : value);
  const toggleCountry = (code: string) => setCountries(value => value.includes(code) ? value.filter(item => item !== code) : value.length < 4 ? [...value, code] : value);
  const rowsFor = (id: string): Array<{ code: string; color: string; entry: DiseaseDatasetSeriesEntry }> => countries.flatMap((code, index) => {
    const entry = datasets[id]?.country_series?.[code];
    return entry?.dates?.length ? [{ code, color: palette[index], entry }] : [];
  });
  const chartOption = (id: string) => {
    const rows = rowsFor(id);
    const ink = dark ? '#8fa8ba' : '#526271';
    return { animation: false, backgroundColor: 'transparent', color: rows.map(row => row.color), legend: { top: 0, type: 'scroll', textStyle: { color: ink } }, grid: { left: 64, right: 24, top: 45, bottom: 75 }, tooltip: { trigger: 'axis', valueFormatter: (value: number) => value == null ? '—' : value.toLocaleString(language) }, xAxis: { type: 'time', axisLabel: { color: ink } }, yAxis: { type: 'value', min: 0, axisLabel: { color: ink, formatter: (value: number) => Intl.NumberFormat(language, { notation: 'compact' }).format(value) } }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 15, height: 20 }], series: rows.map(row => ({ name: `${placeName(row.entry)} · ${row.entry.period_granularity ?? 'source period'}`, type: 'line', showSymbol: false, connectNulls: false, data: row.entry.dates.map((date, index) => [date, unit === 'rate' ? row.entry.incidence_rates?.[index] : row.entry.cases?.[index]]).filter(point => point[1] != null), lineStyle: { width: 2 }, emphasis: { focus: 'series' } })) };
  };
  const share = async () => {
    const url = new URL(location.href); url.searchParams.set('d', selected.join(',')); url.searchParams.set('c', countries.join(',')); url.searchParams.set('u', unit);
    await navigator.clipboard.writeText(url.toString()); setCopyState(true); window.setTimeout(() => setCopyState(false), 2000);
  };
  const downloadPng = (id: string) => {
    const url = refs.current[id]?.getEchartsInstance()?.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: dark ? '#142839' : '#fffdfa' });
    if (!url) return;
    const a = document.createElement('a'); a.href = url; a.download = `gids-compare-${id.toLowerCase()}.png`; a.click();
  };
  const downloadCsv = (id: string) => {
    const rows = rowsFor(id);
    const lines = ['disease_id,country_code,date,cases,per_100k,period_granularity'];
    rows.forEach(({ code, entry }) => entry.dates.forEach((date, index) => lines.push(`${id},${code},${date},${entry.cases?.[index] ?? ''},${entry.incidence_rates?.[index] ?? ''},${entry.point_granularities?.[index] ?? entry.period_granularity ?? ''}`)));
    const url = URL.createObjectURL(new Blob(['\uFEFF', lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `gids-compare-${id.toLowerCase()}.csv`; a.click(); URL.revokeObjectURL(url);
  };
  return <div className="compare-workspace">
    <p className="disease-trend-note">{label.note}</p>
    <div className="compare-controls"><fieldset><legend>{label.diseases}</legend><div className="compare-choice-list">{diseases.map(d => <label key={d.id}><input type="checkbox" checked={selected.includes(d.id)} disabled={!selected.includes(d.id) && selected.length >= 2} onChange={() => toggleDisease(d.id)} />{diseaseName(d.id)}</label>)}</div></fieldset><fieldset><legend>{label.countries}</legend><div className="compare-choice-list">{available.map(([code, entry]) => <label key={code}><input type="checkbox" checked={countries.includes(code)} disabled={!countries.includes(code) && countries.length >= 4} onChange={() => toggleCountry(code)} />{placeName(entry)} ({code})</label>)}</div></fieldset></div>
    <div className="disease-trend-controls" role="group" aria-label={label.unit}><button type="button" aria-pressed={unit === 'count'} onClick={() => setUnit('count')}>{label.count}</button><button type="button" aria-pressed={unit === 'rate'} onClick={() => setUnit('rate')}>{label.rate}</button><button type="button" onClick={share}>{copyState ? '✓' : label.share}</button></div>
    {unit === 'rate' && <p className="disease-trend-note">{label.missing}</p>}
    {loading && <div className="chart-loading-panel" role="status" aria-busy="true" style={{ height: 280 }} />}
    {!loading && selected.length && countries.length >= 2 ? selected.map(id => <section className="compare-chart" key={id}><div className="disease-trend-heading"><h2 className="figure-title">{diseaseName(id)}</h2><div className="disease-trend-actions"><button type="button" onClick={() => downloadPng(id)}>{label.png}</button><button type="button" onClick={() => downloadCsv(id)}>{label.csv}</button></div></div><EChartsReact ref={instance => { refs.current[id] = instance; }} echarts={echarts} option={chartOption(id)} notMerge style={{ height: 380, width: '100%' }} /></section>) : !loading ? <p className="disease-trend-empty">{label.empty}</p> : null}
  </div>;
}
