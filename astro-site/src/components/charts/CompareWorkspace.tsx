import { useEffect, useMemo, useRef, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import ChartResultActions from './ChartResultActions';
import { pngFromCanvases } from './chartExport';
import { loadDiseaseDataset, type DiseaseDataset, type DiseaseDatasetSeriesEntry } from './diseaseDataset';
import { useSurveillanceSummary } from '../public/useSurveillanceSummary';
import { matchesDiseaseSearch } from '../../lib/disease-search';
import GIDSLoader from '../GIDSLoader';
import './compare-workspace.css';
import { comparisonAreaCodes, comparisonAreaGroups, type CompareAreaOption, comparisonPoints, reconcileComparisonCountries, liveCompareDiseaseOptions, type CompareDiseaseOption } from './compareDiseaseOptions';

type Lang = 'en' | 'zh' | 'fr';
const t = {
  en: { diseases: 'Diseases', countries: 'Geographic areas', diseaseHint: 'Select up to two', countryHint: 'Select two to four areas; expand an area to choose its published lower-level administrative units', subdivisions: 'Lower-level units', searchDiseases: 'Search diseases', searchCountries: 'Search geographic areas', noMatches: 'No matches', available: 'top-level areas', availableOne: 'top-level area', results: 'Comparison results', unit: 'Measure', count: 'Reported cases', rate: 'Per 100k, where published', series: 'Comparison series', empty: 'Select a disease and at least two geographic areas with published series.', missing: 'Rates appear only where a source publishes a usable population denominator.', note: 'Lines retain each source’s reporting period and case definition. Compare aligned dates and methods before interpreting differences.' },
  zh: { diseases: '疾病', countries: '地理区域', diseaseHint: '最多选择 2 种', countryHint: '选择 2–4 个区域；展开区域可选择已有数据的下一级行政单位', subdivisions: '下一级行政单位', searchDiseases: '搜索疾病', searchCountries: '搜索地理区域', noMatches: '没有匹配项', available: '个上级区域', availableOne: '个上级区域', results: '对比结果', unit: '指标', count: '报告病例数', rate: '每 10 万人（有可用分母时）', series: '对比序列', empty: '请选择疾病及至少两个有公开序列的地理区域。', missing: '仅在来源提供可用人口分母时显示每 10 万人口数值。', note: '曲线保留各来源的报告周期和病例定义；解释差异前请核对日期与统计方法。' },
  fr: { diseases: 'Maladies', countries: 'Zones géographiques', diseaseHint: 'Deux au maximum', countryHint: 'Choisissez 2 à 4 zones ; développez une zone pour sélectionner ses unités administratives de niveau inférieur publiées', subdivisions: 'Unités de niveau inférieur', searchDiseases: 'Rechercher une maladie', searchCountries: 'Rechercher une zone géographique', noMatches: 'Aucun résultat', available: 'zones de niveau supérieur', availableOne: 'zone de niveau supérieur', results: 'Résultats de la comparaison', unit: 'Mesure', count: 'Cas déclarés', rate: 'Pour 100 000, si publié', series: 'Séries comparées', empty: 'Choisissez une maladie et au moins deux zones géographiques avec des séries publiées.', missing: 'Le taux apparaît seulement lorsqu’un dénominateur valide est disponible.', note: 'Les courbes conservent la période et la définition de chaque source. Vérifiez les méthodes avant toute comparaison.' },
};
const palette = ['#0f766e', '#2563eb', '#c06127', '#9b4dcc'];

function CompareResultChart({ id, title, names, option, lang, share, dataHref, register }: {
  id: string;
  title: string;
  names?: Partial<Record<Lang, string>>;
  option: any;
  lang: Lang;
  share: () => string;
  dataHref?: string;
  register: (instance: any) => void;
}) {
  const shell = useRef<HTMLElement>(null);
  const plot = useRef<HTMLDivElement>(null);
  return (
    <section ref={shell} className="compare-chart panel-fullscreen" data-chart-title={title} data-disease-names={names ? JSON.stringify(names) : undefined}>
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

export default function CompareWorkspace({ diseases: identities, language = 'en' }: { diseases: CompareDiseaseOption[]; language?: Lang }) {
  const [selected, setSelected] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [unit, setUnit] = useState<'count' | 'rate'>('count');
  const [datasets, setDatasets] = useState<Record<string, DiseaseDataset>>({});
  const publication = useSurveillanceSummary('/site-data/meta.json');
  const diseases = useMemo(() => liveCompareDiseaseOptions(identities, publication.data?.disease_coverage, datasets), [identities, publication.data, datasets]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [removedCountries, setRemovedCountries] = useState<string[]>([]);
  const [dark, setDark] = useState(false);
  const [compact, setCompact] = useState(false);
  const [ready, setReady] = useState(false);
  const [diseaseQuery, setDiseaseQuery] = useState('');
  const [countryQuery, setCountryQuery] = useState('');
  const [expandedAreas, setExpandedAreas] = useState<Record<string, boolean>>({});
  const refs = useRef<Record<string, any>>({});
  const seededCountries = useRef(false);
  const initialized = useRef(false);
  const urlCountriesProvided = useRef(false);
  const urlCountries = useRef<string[]>([]);
  const label = t[language];
  const diseaseName = (id: string) => {
    const disease = diseases.find(item => item.id === id);
    if (!disease) return id;
    return language === 'zh' ? disease.zh : language === 'fr' ? disease.fr : disease.en;
  };
  const diseaseNames = (id: string) => {
    const disease = diseases.find(item => item.id === id);
    if (!disease) return undefined;
    return { en: disease.en, zh: disease.zh, fr: disease.fr };
  };
  const placeName = (area: CompareAreaOption) => area.names[language];
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
    if (initialized.current || (!publication.data && !publication.error)) return;
    initialized.current = true;
    const availableDiseases = liveCompareDiseaseOptions(identities, publication.data?.disease_coverage);
    const params = new URLSearchParams(location.search);
    const ids = params.get('d')?.split(',').filter(id => identities.some(d => d.id === id)).slice(0, 2) ?? [];
    urlCountriesProvided.current = params.has('c');
    urlCountries.current = comparisonAreaCodes(params.get('c'));
    setSelected(params.has('d') ? ids : availableDiseases.filter(d => (d.countries ?? 0) > 0).slice(0, 1).map(d => d.id));
    setCountries(urlCountries.current);
    setUnit(params.get('u') === 'rate' ? 'rate' : 'count');
    setReady(true);
  }, [identities, publication.data, publication.error]);
  useEffect(() => {
    if (!selected.length) { setDatasets({}); setLoading(false); setLoadError(false); return; }
    let cancelled = false;
    setLoading(true); setLoadError(false);
    Promise.all(selected.map(async id => [id, await loadDiseaseDataset(`/site-data/diseases/${id.toLowerCase()}.json`)] as const))
      .then(entries => { if (!cancelled) { setDatasets(Object.fromEntries(entries)); setLoading(false); } })
      .catch(() => { if (!cancelled) { setDatasets({}); setLoading(false); setLoadError(true); } });
    return () => { cancelled = true; };
  }, [selected, attempt]);
  const diseaseHref = (id: string) => diseases.find(item => item.id === id)?.href;
  const areaGroups = useMemo(() => comparisonAreaGroups(selected, datasets, publication.data?.countries), [selected, datasets, publication.data]);
  const available = useMemo(() => areaGroups.flatMap(group => [group.area, ...group.children].filter(area => area.selectable)), [areaGroups]);
  const areaByCode = useMemo(() => new Map(available.map(area => [area.code, area])), [available]);
  const matchingDiseases = diseases.filter(disease => matchesDiseaseSearch(disease, diseaseQuery));
  const query = countryQuery.trim().toLocaleLowerCase();
  const areaMatches = (area: CompareAreaOption) => `${area.code} ${Object.values(area.names).join(' ')}`.toLocaleLowerCase().includes(query);
  const matchingGroups = areaGroups.flatMap(group => {
    const parentMatches = areaMatches(group.area);
    const children = parentMatches ? group.children : group.children.filter(areaMatches);
    return parentMatches || children.length ? [{ ...group, children }] : [];
  });
  useEffect(() => {
    if (!ready || loading || loadError || !selected.length || selected.some(id => !datasets[id])) return;
    if (!seededCountries.current) {
      seededCountries.current = true;
      if (!urlCountriesProvided.current) { setCountries(available.filter(area => !area.parentCode).concat(available.filter(area => area.parentCode)).slice(0, 2).map(area => area.code)); return; }
    }
    const next = reconcileComparisonCountries(countries, available.map(area => area.code));
    if (next.removed.length) { setCountries(next.countries); setRemovedCountries(next.removed); }
  }, [available, ready, loading, loadError, countries, selected, datasets]);
  useEffect(() => {
    if (!ready) return;
    const url = new URL(location.href);
    url.searchParams.set('d', selected.join(',')); url.searchParams.set('c', countries.join(',')); url.searchParams.set('u', unit);
    if (url.toString() !== location.href) {
      history.replaceState(null, '', url);
      window.dispatchEvent(new Event('gids:urlchange'));
    }
  }, [ready, selected, countries, unit]);
  const toggleDisease = (id: string) => setSelected(value => value.includes(id) ? value.filter(item => item !== id) : value.length < 2 ? [...value, id] : value);
  const toggleCountry = (code: string) => setCountries(value => value.includes(code) ? value.filter(item => item !== code) : value.length < 4 ? [...value, code] : value);
  const rowsFor = (id: string): Array<{ code: string; color: string; entry: DiseaseDatasetSeriesEntry }> => countries.flatMap((code, index) => {
    const entry = datasets[id]?.country_series?.[code];
    const values = unit === 'rate' ? entry?.incidence_rates : entry?.cases;
    return entry?.dates?.length && values?.some(value => Number.isFinite(value)) ? [{ code, color: palette[index], entry }] : [];
  });
  const chartOption = (id: string) => {
    const rows = rowsFor(id);
    const ink = dark ? '#8fa8ba' : '#526271';
    return { animation: false, backgroundColor: 'transparent', color: rows.map(row => row.color), legend: { top: 0, type: 'scroll', textStyle: { color: ink } }, grid: { left: compact ? 46 : 64, right: compact ? 12 : 24, top: 45, bottom: 75 }, tooltip: { trigger: 'axis', valueFormatter: (value: number) => value == null ? '—' : value.toLocaleString(language) }, xAxis: { type: 'time', axisLabel: { color: ink, hideOverlap: true } }, yAxis: { type: 'value', min: 0, axisLabel: { color: ink, formatter: (value: number) => Intl.NumberFormat(language, { notation: 'compact' }).format(value) } }, dataZoom: [{ type: 'inside' }, { type: 'slider', bottom: 15, height: 20 }], series: rows.map(row => ({ name: `${areaByCode.get(row.code)?.names[language] ?? row.code} · ${periodName(row.entry.period_granularity)}`, type: 'line', showSymbol: false, connectNulls: false, data: comparisonPoints(row.entry.dates, unit === 'rate' ? row.entry.incidence_rates ?? [] : row.entry.cases ?? []), lineStyle: { width: 2 }, emphasis: { focus: 'series' } })) };
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
    {removedCountries.length > 0 && <p className="disease-trend-note" role="status">{language === 'zh' ? `当前疾病没有 ${removedCountries.join('、')} 的可用序列，已移除这些选择。请重新选择参与对比的地理区域。` : language === 'fr' ? `Sélections retirées, sans séries disponibles : ${removedCountries.join(', ')}. Choisissez d’autres zones géographiques.` : `Removed areas without available series: ${removedCountries.join(', ')}. Choose areas for the current diseases.`}</p>}
    <div className="compare-controls">
      <div className="compare-filter-panel">
        <fieldset className="compare-filter-group">
          <legend>{label.diseases} <span>{selected.length}/2</span></legend>
          <p className="compare-filter-hint">{label.diseaseHint}</p>
          <input className="compare-search" type="search" aria-label={label.searchDiseases} placeholder={label.searchDiseases} value={diseaseQuery} onChange={event => setDiseaseQuery(event.target.value)} />
          <div className="compare-choice-list">{matchingDiseases.map(d => <label key={d.id} className={selected.includes(d.id) ? 'is-selected' : ''}><input type="checkbox" checked={selected.includes(d.id)} disabled={!selected.includes(d.id) && selected.length >= 2} onChange={() => toggleDisease(d.id)} /><span>{diseaseName(d.id)}</span><small>{d.countries ?? '—'} {d.countries === 1 ? label.availableOne : label.available}</small></label>)}{!matchingDiseases.length && <p className="compare-no-matches">{label.noMatches}</p>}</div>
        </fieldset>
      </div>
      <div className="compare-filter-panel"><fieldset className="compare-filter-group">
        <legend>{label.countries} <span>{countries.filter(code => areaByCode.has(code)).length}/4</span></legend>
        <p className="compare-filter-hint">{label.countryHint}</p>
        <input className="compare-search" type="search" aria-label={label.searchCountries} placeholder={label.searchCountries} value={countryQuery} onChange={event => { setCountryQuery(event.target.value); setExpandedAreas({}); }} />
        <div className="compare-choice-list compare-area-list">
          {matchingGroups.map(({ area, children }) => {
            const expanded = expandedAreas[area.code] ?? (Boolean(query) || children.some(child => countries.includes(child.code)));
            const choice = (item: CompareAreaOption) => <label key={item.code} className={countries.includes(item.code) ? 'is-selected' : ''}>
              <input type="checkbox" checked={countries.includes(item.code)} disabled={!countries.includes(item.code) && countries.length >= 4} onChange={() => toggleCountry(item.code)} />
              <span>{placeName(item)}</span><small>{item.code}</small>
            </label>;
            return <div key={area.code} className="compare-area-group">
              <div className="compare-area-heading">
                {area.selectable ? choice(area) : <p className="compare-area-parent">{placeName(area)}<small>{area.code}</small></p>}
                {children.length > 0 && <button type="button" className="compare-area-expand" aria-label={`${placeName(area)} · ${label.subdivisions} (${children.length})`} aria-expanded={expanded} aria-controls={`compare-children-${area.code}`} onClick={() => {
                  setExpandedAreas(value => ({ ...value, [area.code]: !expanded }));
                }}>{label.subdivisions} ({children.length}) <span aria-hidden="true">{expanded ? '−' : '+'}</span></button>}
              </div>
              {children.length > 0 && <div id={`compare-children-${area.code}`} className="compare-area-children" hidden={!expanded}>{children.map(choice)}</div>}
            </div>;
          })}
          {!matchingGroups.length && <p className="compare-no-matches">{label.noMatches}</p>}
        </div>
      </fieldset></div>
    </div>
    <div className="compare-results-heading"><div><p className="figure-kicker">{label.results}</p><h2 className="figure-title">{selected.map(diseaseName).join(' / ') || label.results}</h2></div><label className="chart-measure">{label.unit}<select aria-label={label.unit} value={unit} onChange={event => setUnit(event.target.value === 'rate' ? 'rate' : 'count')}><option value="count">{label.count}</option><option value="rate">{label.rate}</option></select></label></div>
    {unit === 'rate' && <p className="disease-trend-note">{label.missing}</p>}
    {(!ready || loading) && <div className="chart-loading-panel" style={{ minHeight: 180 }}><GIDSLoader language={language} /></div>}
    {loadError && <p className="disease-trend-empty compare-chart" role="status">{language === 'zh' ? '暂时无法读取对比数据。' : language === 'fr' ? 'Les données de comparaison sont indisponibles.' : 'Comparison data could not load.'} <button type="button" className="underline" onClick={() => setAttempt(value => value + 1)}>{language === 'zh' ? '重试' : language === 'fr' ? 'Réessayer' : 'Retry'}</button></p>}
    {ready && !loading && !loadError && (selected.length && countries.length >= 2 ? selected.map(id => rowsFor(id).length >= 2 ? <CompareResultChart key={id} id={id} title={diseaseName(id)} names={diseaseNames(id)} option={chartOption(id)} lang={language} share={share} dataHref={diseaseHref(id)} register={instance => { refs.current[id] = instance; }} /> : <p key={id} className="disease-trend-empty compare-chart" role="status">{diseaseName(id)} · {language === 'zh' ? `当前指标仅有 ${rowsFor(id).length} 个已选区域有有效数据，请选择至少两个。` : language === 'fr' ? `Données valides pour ${rowsFor(id).length} zone(s) sélectionnée(s) ; choisissez-en au moins deux.` : `Only ${rowsFor(id).length} selected areas have data for this measure. Select at least two.`}</p>) : <p className="disease-trend-empty compare-chart">{label.empty}</p>)}
  </div>;
}
