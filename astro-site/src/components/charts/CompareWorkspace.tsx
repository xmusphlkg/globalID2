import { useEffect, useMemo, useRef, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import ChartResultActions from './ChartResultActions';
import { pngFromCanvases } from './chartExport';
import { loadDiseaseDataset, type DiseaseDataset, type DiseaseDatasetSeriesEntry } from './diseaseDataset';

type DiseaseOption = { id: string; slug: string; href?: string; en: string; zh: string; fr: string; countries: number };
type Lang = 'en' | 'zh' | 'fr';
const t = {
  en: { diseases: 'Diseases', countries: 'Countries', diseaseHint: 'Select up to two', countryHint: 'Select two to four', searchDiseases: 'Search diseases', searchCountries: 'Search countries', noMatches: 'No matches', available: 'reporting countries', results: 'Comparison results', unit: 'Measure', count: 'Reported cases', rate: 'Per 100k, where published', series: 'Comparison series', empty: 'Select a disease and at least two countries with published series.', missing: 'Rates appear only where a source publishes a usable population denominator.', note: 'Lines retain each source’s reporting period and case definition. Compare aligned dates and methods before interpreting differences.' },
  zh: { diseases: '疾病', countries: '国家与地区', diseaseHint: '最多选择 2 种', countryHint: '选择 2–4 个', searchDiseases: '搜索疾病', searchCountries: '搜索国家与地区', noMatches: '没有匹配项', available: '个报告国家/地区', results: '对比结果', unit: '指标', count: '报告病例数', rate: '每 10 万人（有可用分母时）', series: '对比序列', empty: '请选择疾病及至少两个有公开序列的国家。', missing: '仅在来源提供可用人口分母时显示每 10 万人口数值。', note: '曲线保留各来源的报告周期和病例定义；解释差异前请核对日期与统计方法。' },
  fr: { diseases: 'Maladies', countries: 'Pays et régions', diseaseHint: 'Deux au maximum', countryHint: 'Choisissez 2 à 4 pays', searchDiseases: 'Rechercher une maladie', searchCountries: 'Rechercher un pays', noMatches: 'Aucun résultat', available: 'pays déclarants', results: 'Résultats de la comparaison', unit: 'Mesure', count: 'Cas déclarés', rate: 'Pour 100 000, si publié', series: 'Séries comparées', empty: 'Choisissez une maladie et au moins deux pays avec des séries publiées.', missing: 'Le taux apparaît seulement lorsqu’un dénominateur valide est disponible.', note: 'Les courbes conservent la période et la définition de chaque source. Vérifiez les méthodes avant toute comparaison.' },
};
const palette = ['#0f766e', '#2563eb', '#c06127', '#9b4dcc'];

function CompareResultChart({ id, title, option, lang, share, dataHref, register }: {
  id: string;
  title: string;
  option: any;
  lang: Lang;
  share: () => string;
  dataHref?: string;
  register: (instance: any) => void;
}) {
  const shell = useRef<HTMLElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  return (
    <section ref={shell} className="compare-chart panel-fullscreen" data-chart-title={title}>
      <div className="compare-chart-heading">
        <h3 className="figure-title">{title}</h3>
        <ChartResultActions
          lang={lang}
          shellRef={shell}
          onShare={share}
          showData={false}
          dataHref={dataHref}
          pngFilename={`gids-compare-${id.toLowerCase()}.png`}
          getPng={() => (plot.current ? pngFromCanvases(plot.current) : null)}
        />
      </div>
      <div className="compare-chart-plot" ref={plot}>
        <EChartsReact ref={register} echarts={echarts} option={option} notMerge style={{ height: 380, width: '100%' }} />
      </div>
    </section>
  );
}

export default function CompareWorkspace({ diseases, language = 'en' }: { diseases: DiseaseOption[]; language?: Lang }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [unit, setUnit] = useState<'count' | 'rate'>('count');
  const [datasets, setDatasets] = useState<Record<string, DiseaseDataset>>({});
  const [loading, setLoading] = useState(false);
  const [dark, setDark] = useState(false);
  const [compact, setCompact] = useState(false);
  const [ready, setReady] = useState(false);
  const [diseaseQuery, setDiseaseQuery] = useState('');
  const [countryQuery, setCountryQuery] = useState('');
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
  const periodName = (value?: string | null) => {
    const period = String(value ?? '').toLowerCase();
    const names: Record<string, [string, string, string]> = {
      day: ['daily', '每日', 'quotidien'], daily: ['daily', '每日', 'quotidien'],
      week: ['weekly', '每周', 'hebdomadaire'], weekly: ['weekly', '每周', 'hebdomadaire'],
      month: ['monthly', '每月', 'mensuel'], monthly: ['monthly', '每月', 'mensuel'],
      year: ['annual', '每年', 'annuel'], annual: ['annual', '每年', 'annuel'], yearly: ['annual', '每年', 'annuel'],
    };
    const labels = names[period];
    return labels ? labels[language === 'zh' ? 1 : language === 'fr' ? 2 : 0] : (value || (language === 'zh' ? '来源报告期' : language === 'fr' ? 'période source' : 'source period'));
  };
  useEffect(() => {
    const update = () => setDark(document.documentElement.getAttribute('data-theme') !== 'light');
    update();
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const resize = () => {
      setCompact(window.innerWidth < 640);
      Object.values(refs.current).forEach(ref => ref?.getEchartsInstance?.()?.resize());
    };
    resize();
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
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
  const diseaseHref = (id: string) => diseases.find(item => item.id === id)?.href;
  const available = useMemo(() => {
    const map = new Map<string, DiseaseDatasetSeriesEntry>();
    selected.forEach(id => Object.entries(datasets[id]?.country_series ?? {}).forEach(([code, entry]) => {
      if (/^[A-Z]{2}$/.test(code) && entry.dates?.length && !map.has(code)) map.set(code, entry);
    }));
    return [...map].sort((a, b) => a[1].name_en.localeCompare(b[1].name_en));
  }, [datasets, selected]);
  const matchingDiseases = diseases.filter(disease => `${disease.en} ${disease.zh} ${disease.fr} ${disease.id}`.toLocaleLowerCase().includes(diseaseQuery.trim().toLocaleLowerCase()));
  const matchingCountries = available.filter(([code, entry]) => `${code} ${entry.name_en} ${entry.name_zh} ${entry.name_fr ?? ''}`.toLocaleLowerCase().includes(countryQuery.trim().toLocaleLowerCase()));
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
    return { animation: false, backgroundColor: 'transparent', color: rows.map(row => row.color), legend: { top: 0, type: 'scroll', textStyle: { color: ink } }, grid: { left: compact ? 46 : 64, right: compact ? 12 : 24, top: 45, bottom: 75 }, tooltip: { trigger: 'axis', valueFormatter: (value: number) => value == null ? '—' : value.toLocaleString(language) }, xAxis: { type: 'time', axisLabel: { color: ink, hideOverlap: true } }, yAxis: { type: 'value', min: 0, axisLabel: { color: ink, formatter: (value: number) => Intl.NumberFormat(language, { notation: 'compact' }).format(value) } }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 15, height: 20 }], series: rows.map(row => ({ name: `${placeName(row.entry)} · ${periodName(row.entry.period_granularity)}`, type: 'line', showSymbol: false, connectNulls: false, data: row.entry.dates.map((date, index) => [date, unit === 'rate' ? row.entry.incidence_rates?.[index] : row.entry.cases?.[index]]).filter(point => point[1] != null), lineStyle: { width: 2 }, emphasis: { focus: 'series' } })) };
  };
  const share = () => {
    const url = new URL(location.href);
    url.searchParams.set('d', selected.join(','));
    url.searchParams.set('c', countries.join(','));
    url.searchParams.set('u', unit);
    return url.toString();
  };
  return <div className="compare-workspace">
    <div className="compare-method-note"><span className="figure-kicker">{language === 'zh' ? '解读提示' : language === 'fr' ? 'Note méthodologique' : 'Interpretation note'}</span><p>{label.note}</p></div>
    <div className="compare-controls">
      <fieldset className="compare-filter-panel"><legend>{label.diseases} <span>{selected.length}/2</span></legend><p className="compare-filter-hint">{label.diseaseHint}</p><input className="compare-search" type="search" aria-label={label.searchDiseases} placeholder={label.searchDiseases} value={diseaseQuery} onChange={event => setDiseaseQuery(event.target.value)} /><div className="compare-choice-list">{matchingDiseases.map(d => <label key={d.id} className={selected.includes(d.id) ? 'is-selected' : ''}><input type="checkbox" checked={selected.includes(d.id)} disabled={!selected.includes(d.id) && selected.length >= 2} onChange={() => toggleDisease(d.id)} /><span>{diseaseName(d.id)}</span><small>{d.countries} {label.available}</small></label>)}{!matchingDiseases.length && <p className="compare-no-matches">{label.noMatches}</p>}</div></fieldset>
      <fieldset className="compare-filter-panel"><legend>{label.countries} <span>{countries.length}/4</span></legend><p className="compare-filter-hint">{label.countryHint}</p><input className="compare-search" type="search" aria-label={label.searchCountries} placeholder={label.searchCountries} value={countryQuery} onChange={event => setCountryQuery(event.target.value)} /><div className="compare-choice-list">{matchingCountries.map(([code, entry]) => <label key={code} className={countries.includes(code) ? 'is-selected' : ''}><input type="checkbox" checked={countries.includes(code)} disabled={!countries.includes(code) && countries.length >= 4} onChange={() => toggleCountry(code)} /><span>{placeName(entry)}</span><small>{code}</small></label>)}{!matchingCountries.length && <p className="compare-no-matches">{label.noMatches}</p>}</div></fieldset>
    </div>
    <div className="compare-results-heading"><div><p className="figure-kicker">{label.results}</p><h2 className="figure-title">{selected.map(diseaseName).join(' / ') || label.results}</h2></div><label className="chart-measure">{label.unit}<select aria-label={label.unit} value={unit} onChange={event => setUnit(event.target.value === 'rate' ? 'rate' : 'count')}><option value="count">{label.count}</option><option value="rate">{label.rate}</option></select></label></div>
    {unit === 'rate' && <p className="disease-trend-note">{label.missing}</p>}
    {loading && <div className="chart-loading-panel" role="status" aria-busy="true" style={{ height: 280 }} />}
    {!loading && selected.length && countries.length >= 2 ? selected.map(id => <CompareResultChart key={id} id={id} title={diseaseName(id)} option={chartOption(id)} lang={language} share={share} dataHref={diseaseHref(id)} register={instance => { refs.current[id] = instance; }} />) : !loading ? <p className="disease-trend-empty compare-chart">{label.empty}</p> : null}
  </div>;
}
