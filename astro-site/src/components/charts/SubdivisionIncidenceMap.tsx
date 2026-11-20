import { useEffect, useId, useMemo, useRef, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echartsMap';
import '../../lib/echartsSurveillance';
import { GraphicComponent, LegendComponent } from 'echarts/components';
import { surveillanceData } from '../../lib/surveillance-data';
import { useChartTheme } from './chartPreferences';
import ChartResultActions from './ChartResultActions';
import { downloadCenterHref } from '../../lib/download-center-link';
import { prepareMapDataset, resultsFor, defaultWindow, sharedScale, restoreWindow, validWindow, comparisonWindow, presetWindow, shiftWindowYear, trendFor, dateBounds, cadenceRank, type MapCatalog, type PopulationDataset, type DiseaseDataset, type Language, type MapWindow, type MissingReason, type RegionResult, type Cadence, type Comparison } from './subdivision-map/model';
import './surveillance/components/surveillance.css';
import './subdivision-map/map.css';
import RegionList from './subdivision-map/RegionList';
import { continuousScale, scaleGraphic, scaleValue } from './subdivision-map/scale';

echarts.use([GraphicComponent, LegendComponent]);

type Geometry = Parameters<typeof echarts.registerMap>[1];
interface Props { countryCode: string; diseaseId?: string; language?: Language; }
const monthNames = (lang: Language) => Array.from({ length: 12 }, (_, index) => lang === 'zh' ? `${index + 1}月` : new Intl.DateTimeFormat(lang, { month: 'short', timeZone: 'UTC' }).format(new Date(Date.UTC(2024, index, 1))));
const labels = {
  en: { title: 'Regional distribution', disease: 'Disease', year: 'Year', start: 'First month', end: 'Last month', metric: 'Measure', rate: 'Reports per 100,000', cases: 'Reported cases', area: 'Region', population: 'Total resident population', observed: 'Reported months', source: 'Population source', reference: 'Reference date', basis: 'Population basis', version: 'Population version', download: 'Download filtered CSV', loading: 'Loading regional data…', unavailable: 'Regional map data are not available in this publication.', noDisease: 'No comparable regional counts are available for this disease.', zero: 'Zero', missing: 'Missing / suppressed', notAnnual: 'Selected-period crude report rates; not annualized or age standardized.', scale: 'The same disease, measure and month range share a color scale across years.', boundary: 'Boundaries', historical: 'Present monitoring units; historical boundary changes are not reconstructed.', formula: 'Period reports ÷ local population in the same year × 100,000. Missing years are not filled.', trend: 'Monthly reports in the selected region', complete: 'regions with a complete value', provisional: 'Provisional', unverified: 'Unverified', selected: 'Selected region', noPopulation: 'No verified local population for this year. Case counts remain available.', fullYear: 'Full year', firstQuarter: 'Jan–Mar' },
  zh: { title: '地区分布', disease: '疾病', year: '年份', start: '开始月份', end: '结束月份', metric: '指标', rate: '每10万人报告数', cases: '报告病例数', area: '地区', population: '当年常住总人口', observed: '可用报告月', source: '人口来源', reference: '人口参照日期', basis: '人口统计口径', version: '人口版本', download: '下载当前筛选 CSV', loading: '正在加载地区数据…', unavailable: '当前发布版本尚无可用地区地图数据。', noDisease: '此疾病暂无可比较的地区病例数。', zero: '零', missing: '缺失／抑制', notAnnual: '所选期间的粗报告率，未年化、未作年龄标化。', scale: '同病种、同指标和月份范围跨年共用色阶。', boundary: '边界', historical: '按当前监测地区展示，未重建历史行政区变更。', formula: '期间病例总数 ÷ 该省同年人口 × 100,000；人口缺年不补值。', trend: '选中地区月度报告趋势', complete: '个地区具有完整数值', provisional: '暂定数据', unverified: '未核实', selected: '选中地区', noPopulation: '缺少此地区当年已核实人口，仍可切换查看病例数。', fullYear: '全年', firstQuarter: '1–3月' },
  fr: { title: 'Répartition régionale', disease: 'Maladie', year: 'Année', start: 'Premier mois', end: 'Dernier mois', metric: 'Indicateur', rate: 'Notifications pour 100 000', cases: 'Cas déclarés', area: 'Région', population: 'Population résidente totale', observed: 'Mois renseignés', source: 'Source démographique', reference: 'Date de référence', basis: 'Base démographique', version: 'Version démographique', download: 'Télécharger le CSV filtré', loading: 'Chargement régional…', unavailable: 'Carte régionale indisponible dans cette publication.', noDisease: 'Aucun décompte régional comparable pour cette maladie.', zero: 'Zéro', missing: 'Manquant / masqué', notAnnual: 'Taux brut de la période, non annualisé et non standardisé sur l’âge.', scale: 'Échelle commune entre années pour la même maladie, mesure et période.', boundary: 'Limites', historical: 'Unités de surveillance actuelles ; changements historiques non reconstruits.', formula: 'Notifications de la période ÷ population locale de la même année × 100 000. Aucune imputation.', trend: 'Notifications mensuelles de la région sélectionnée', complete: 'régions avec valeur complète', provisional: 'Provisoire', unverified: 'Non vérifié', selected: 'Région sélectionnée', noPopulation: 'Population locale vérifiée indisponible pour cette année. Les cas restent consultables.', fullYear: 'Année entière', firstQuarter: 'Jan–mars' },
};
const calendarLabels = {
  en: { from: 'Start date', to: 'End date', view: 'Group trend by', compare: 'Compare with', none: 'No comparison', previous_year: 'Same period last year', previous_period: 'Previous period', custom: 'Custom range', compareFrom: 'Comparison start', compareTo: 'Comparison end', current: 'Selected range', previous: 'Comparison', change: 'Change', monthPreset: 'Select month', quarterPreset: 'Select quarter', yearPreset: 'Full year', actual: 'Complete source periods included', periodRule: 'Only whole source periods are included. Boundary weeks, months, quarters or years are not split.', invalid: 'Choose valid start and end dates in chronological order.', annualPopulations: 'Population and rate contribution by reporting year', formula: 'For each reporting year: period reports ÷ local population for that year × 100,000; contributions are added across years. Not annualized or age standardized.', observed: 'Available source periods', trend: 'Reports in the selected region', equalDuration: 'Comparison periods contain different numbers of source periods; compare with care.', noPopulation: 'Verified population is missing for at least one reporting year. Case counts remain available.', scale: 'The same disease, measure and range share a scale across years and comparison periods.', focus: 'Focus on mainland', reset: 'Reset map', populationPrecision: 'Japanese population totals are published in thousands and converted to persons at that original precision.', alignment: 'Comparison curves align by period order; hover to see each period’s actual dates.', weeklyGrouping: 'Weekly reports are grouped by the week’s start date; whole weeks are retained.' },
  zh: { from: '开始日期', to: '结束日期', view: '趋势汇总周期', compare: '对比范围', none: '不对比', previous_year: '上年同期', previous_period: '上一期间', custom: '自定义范围', compareFrom: '对比开始日期', compareTo: '对比结束日期', current: '当前范围', previous: '对比范围', change: '变化', monthPreset: '选择月份', quarterPreset: '选择季度', yearPreset: '全年', actual: '实际纳入的完整报告期', periodRule: '仅纳入完整原始报告期；边界处的周、月、季度或年度不会被拆分。', invalid: '请选择有效日期，开始日期不能晚于结束日期。', annualPopulations: '按报告年列出人口及报告率贡献', formula: '按报告年分别计算：期间报告数 ÷ 当地同年人口 × 100,000；跨年时累加各年贡献。未年化、未作年龄标化。', observed: '可用原始报告期', trend: '选中地区报告趋势', equalDuration: '两段范围包含的原始报告期数量不同，比较时请核对实际日期。', noPopulation: '至少一个报告年缺少已核实的当地人口；仍可查看病例数。', scale: '同病种、同指标、同一日期范围跨年及对比期间共用色阶。', focus: '聚焦本土', reset: '重置地图', populationPrecision: '日本官方人口以千人为单位发布，此处按原始精度换算为人。', alignment: '对比曲线按期间顺序对齐；悬停可查看各自实际日期。', weeklyGrouping: '周报告按报告期开始日期归组，保留整周，不拆分病例。' },
  fr: { from: 'Date de début', to: 'Date de fin', view: 'Regrouper la tendance', compare: 'Comparer avec', none: 'Sans comparaison', previous_year: 'Même période, année précédente', previous_period: 'Période précédente', custom: 'Dates personnalisées', compareFrom: 'Début de comparaison', compareTo: 'Fin de comparaison', current: 'Période sélectionnée', previous: 'Comparaison', change: 'Variation', monthPreset: 'Choisir un mois', quarterPreset: 'Choisir un trimestre', yearPreset: 'Année entière', actual: 'Périodes sources complètes incluses', periodRule: 'Seules les périodes sources entières sont incluses, sans fractionner semaines, mois, trimestres ou années.', invalid: 'Choisissez des dates valides dans l’ordre chronologique.', annualPopulations: 'Population et contribution au taux par année de déclaration', formula: 'Pour chaque année de déclaration : notifications ÷ population locale de cette année × 100 000 ; somme des contributions entre années. Sans annualisation ni standardisation sur l’âge.', observed: 'Périodes sources renseignées', trend: 'Notifications dans la région sélectionnée', equalDuration: 'Le nombre de périodes sources diffère entre les deux intervalles.', noPopulation: 'Population locale vérifiée absente pour au moins une année. Les cas restent consultables.', scale: 'Échelle commune entre années et périodes comparées pour une même maladie, mesure et durée.', focus: 'Centrer sur le territoire principal', reset: 'Réinitialiser la carte', populationPrecision: 'Les populations japonaises, publiées en milliers, sont converties en personnes à cette précision.', alignment: 'Courbes alignées par ordre des périodes ; survolez pour voir les dates réelles.', weeklyGrouping: 'Les semaines sont regroupées par leur date de début et restent entières.' },
};
const cadenceLabels: Record<Language, Record<Cadence, string>> = { en: { weekly: 'Weekly', monthly: 'Monthly', quarterly: 'Quarterly', annual: 'Annual' }, zh: { weekly: '周度', monthly: '月度', quarterly: '季度', annual: '年度' }, fr: { weekly: 'Hebdomadaire', monthly: 'Mensuel', quarterly: 'Trimestriel', annual: 'Annuel' } };
const workspaceLabels = {
  en: { map: 'Map', trend: 'Trend', table: 'Data', methods: 'Sources and methods', available: 'regions available', shortRate: 'Not annualized or age standardized', periodDifference: 'Unequal source-period counts', population: 'Population' },
  zh: { map: '地图', trend: '趋势', table: '数据', methods: '来源与计算口径', available: '地区可用', shortRate: '未年化 · 未年龄标化', periodDifference: '两段报告期数量不同', population: '人口' },
  fr: { map: 'Carte', trend: 'Tendance', table: 'Données', methods: 'Sources et méthode', available: 'régions disponibles', shortRate: 'Non annualisé, sans standardisation par âge', periodDifference: 'Nombre de périodes différent', population: 'Population' },
};
const reasonLabels: Record<Language, Record<MissingReason, string>> = {
  en: { not_covered: 'Not covered', not_comparable: 'Different case definition', missing_period: 'Missing source period', suppressed: 'Missing or suppressed value', missing_population: 'No same-year local population', overlap: 'Overlapping report periods', incomplete: 'Incomplete reports', no_complete_period: 'No whole source period in this range', invalid_period: 'Unverified report period' },
  zh: { not_covered: '未覆盖', not_comparable: '病例口径不同', missing_period: '缺少原始报告期', suppressed: '含缺失或被抑制值', missing_population: '缺少同年当地人口', overlap: '报告期重叠', incomplete: '报告不完整', no_complete_period: '范围内没有完整原始报告期', invalid_period: '报告期尚未核实' },
  fr: { not_covered: 'Non couvert', not_comparable: 'Définition différente', missing_period: 'Période source manquante', suppressed: 'Valeur manquante ou masquée', missing_population: 'Population locale annuelle absente', overlap: 'Périodes superposées', incomplete: 'Déclarations incomplètes', no_complete_period: 'Aucune période source complète', invalid_period: 'Période non vérifiée' },
};
const populationBasis: Record<string, [string, string, string]> = {
  october_official_census_rebased_resident_population: ['1 October population officially rebased after census', '10月1日居民人口（官方普查回溯修订）', 'Population au 1er octobre révisée après recensement'],
  october_estimated_resident_population: ['1 October total resident population estimate', '10月1日常住总人口估计', 'Population résidente totale estimée au 1er octobre'],
  year_end_resident_population_ckm: ['Year-end population with official disclosure protection', '年末居民人口（官方披露保护）', 'Population en fin d’année avec protection statistique officielle'],
  year_end_resident_population: ['Year-end resident population', '年末常住人口', 'Population résidente en fin d’année'],
  june_estimated_resident_population: ['June estimated resident population', '6月末常住人口估计', 'Population résidente estimée en juin'],
  latest_observed_quarter_same_year: ['Latest observed quarter in the same year', '同年最近已观测季度人口', 'Dernier trimestre observé de la même année'],
  july_estimated_resident_population: ['1 July resident population estimate', '7月1日常住人口估计', 'Population résidente estimée au 1er juillet'],
  census_resident_population: ['Census resident population', '人口普查常住人口', 'Population résidente du recensement'],
};

export default function SubdivisionIncidenceMap({ countryCode, diseaseId, language = 'en' }: Props) {
  const lang = language, t = { ...labels[lang], ...calendarLabels[lang] }, code = countryCode.toUpperCase();
  const uid = useId().replace(/:/g, '');
  const shell = useRef<HTMLDivElement>(null), mapRef = useRef<any>(null);
  const [tab, setTab] = useState<'map' | 'trend' | 'table'>('map');
  const [fullscreen, setFullscreen] = useState(false);
  const retainedRange = useRef<MapWindow | null>(null), comparisonRestored = useRef(false);
  const theme = useChartTheme();
  const [catalog, setCatalog] = useState<MapCatalog | null>(null);
  const [population, setPopulation] = useState<PopulationDataset | null>(null);
  const [mapName, setMapName] = useState('');
  const [dataset, setDataset] = useState<DiseaseDataset | null>(null);
  const [selectedDisease, setSelectedDisease] = useState('');
  const [window, setWindow] = useState<MapWindow | null>(null);
  const [selected, setSelected] = useState('');
  const [comparison, setComparison] = useState<Comparison>('previous_year');
  const [customComparison, setCustomComparison] = useState({ from: '', to: '' });
  const [mapFocused, setMapFocused] = useState(code === 'JP');
  const [status, setStatus] = useState<'loading' | 'absent' | 'ready' | 'error'>('loading');
  const [failed, setFailed] = useState(false);
  const saved = useMemo(() => typeof globalThis.window === 'undefined' ? new URLSearchParams() : new URLSearchParams(globalThis.window.location.search), []);
  useEffect(() => {
    const requested = saved.get('mapTab');
    if (requested === 'map' || requested === 'trend' || requested === 'table') setTab(requested);
  }, [saved]);
  const name = (region: { code: string; name_en: string; name_zh?: string }) => lang === 'zh' ? region.name_zh || region.name_en : region.name_en;
  const number = (value: number | null | undefined, digits = 1) => value == null ? '—' : value.toLocaleString(lang, digits === 1 && value !== 0 && Math.abs(value) < .1 ? { maximumSignificantDigits: 2 } : { maximumFractionDigits: digits });
  const reason = (row: RegionResult) => row.reasons.filter(item => window?.metric === 'rate' || item !== 'missing_population').map(item => reasonLabels[lang][item]).join(' · ');

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === shell.current);
    document.addEventListener('fullscreenchange', changed);
    return () => document.removeEventListener('fullscreenchange', changed);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    retainedRange.current = null; comparisonRestored.current = false;
    setMapFocused(code === 'JP');
    setStatus('loading'); setCatalog(null); setDataset(null); setWindow(null); setPopulation(null); setMapName('');
    void (async () => {
      const index = await surveillanceData.read<{ countries: Array<{ code: string; path: string }> }>('/site-data/subdivision-maps/index.json', { signal: controller.signal });
      const entry = index.countries.find(item => item.code === code);
      if (!entry) { setStatus('absent'); return; }
      const current = await surveillanceData.read<MapCatalog>(entry.path, { signal: controller.signal });
      const [populations, geometry] = await Promise.all([
        surveillanceData.read<PopulationDataset>(current.population_path, { signal: controller.signal }),
        surveillanceData.read<Geometry>(current.boundary.path, { signal: controller.signal }),
      ]);
      if (controller.signal.aborted) return;
      if (current.schema_version !== 1 || current.country_code !== code || populations.country_code !== code) throw new Error('Map publication mismatch');
      const key = `subdivision-${code}-${current.boundary.version}`;
      // Keep every US state in its source longitude/latitude. Alaska and
      // Hawaii must remain real geographic features rather than inset art.
      echarts.registerMap(key, geometry);
      setMapName(key); setPopulation(populations); setCatalog(current);
      const requested = saved.get('mapDisease') || diseaseId;
      setSelectedDisease(current.diseases.find(item => item.id === requested)?.id
        || current.diseases.find(item => item.id === diseaseId)?.id
        || (diseaseId ? '' : current.diseases.find(item => item.id === 'D028')?.id || current.diseases[0]?.id || ''));
      setStatus('ready');
    })().catch(() => { if (!controller.signal.aborted) setStatus('error'); });
    return () => controller.abort();
  }, [code, diseaseId, saved]);

  useEffect(() => {
    const controller = new AbortController();
    setDataset(null); setWindow(null); setFailed(false);
    const entry = catalog?.diseases.find(item => item.id === selectedDisease);
    if (!entry || !catalog || !population) return () => controller.abort();
    void surveillanceData.read<DiseaseDataset>(entry.path, { signal: controller.signal, retain: false }).then(next => {
      if (controller.signal.aborted) return;
      if (next.country_code !== code || next.disease_id !== selectedDisease || next.schema_version !== 1) throw new Error('Map disease mismatch');
      prepareMapDataset(next);
      const bounds = dateBounds(next), prior = retainedRange.current;
      const keepRange = prior && prior.from >= bounds.from && prior.to <= bounds.to
        && resultsFor(catalog, next, population, prior).some(row => row.cases !== null);
      const restored = keepRange ? { ...prior! }
        : restoreWindow(prior ? new URLSearchParams() : saved, defaultWindow(catalog, next, population), next.years, bounds);
      if (cadenceRank[restored.view] < cadenceRank[next.cadence]) restored.view = next.cadence;
      setDataset(next); setWindow(restored);
      if (!comparisonRestored.current) {
        comparisonRestored.current = true;
        const mode = saved.get('mapCompare');
        if (['none', 'previous_year', 'previous_period', 'custom'].includes(mode || '')) setComparison(mode as Comparison);
        const previous = comparisonWindow(restored, 'previous_year')!;
        setCustomComparison({ from: saved.get('mapCompareFrom') || previous.from, to: saved.get('mapCompareTo') || previous.to });
      }
      setSelected(previous => catalog.regions.find(item => item.code === previous)?.code || catalog.regions.find(item => item.code === saved.get('mapRegion'))?.code || catalog.regions.find(item => next.regions[item.code])?.code || '');
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [catalog, population, code, selectedDisease, saved]);

  useEffect(() => { if (window && validWindow(window)) retainedRange.current = window; }, [window]);

  const rangeValid = !!window && validWindow(window);
  const compareWindow = useMemo(() => rangeValid && window ? comparisonWindow(window, comparison, customComparison) : null, [window, rangeValid, comparison, customComparison]);
  // Trend grouping affects only the trend. Totals and shared map scales depend
  // on dates and metric, so a view-only change must not rebuild these values.
  const rows = useMemo(() => catalog && dataset && population && window && rangeValid ? resultsFor(catalog, dataset, population, window) : [], [catalog, dataset, population, window?.from, window?.to, window?.metric, rangeValid]);
  const compareRows = useMemo(() => catalog && dataset && population && compareWindow ? resultsFor(catalog, dataset, population, compareWindow) : [], [catalog, dataset, population, compareWindow?.from, compareWindow?.to, compareWindow?.metric]);
  const compareByCode = useMemo(() => new Map(compareRows.map(row => [row.region.code, row])), [compareRows]);
  const scale = useMemo(() => catalog && dataset && population && window && rangeValid ? sharedScale(catalog, dataset, population, window, compareWindow) : continuousScale(0), [catalog, dataset, population, window?.from, window?.to, window?.metric, rangeValid, compareWindow?.from, compareWindow?.to, compareWindow?.metric]);
  const rowByCode = useMemo(() => new Map(rows.map(row => [row.region.code, row])), [rows]);
  const active = rowByCode.get(selected);
  const dark = theme === 'dark';
  const colors = dark ? ['#284f53', '#326d74', '#44989f', '#75c4cb', '#b7e9e9'] : ['#e0f0f1', '#afd4d8', '#75b4bd', '#398293', '#15566d'];
  const text = dark ? '#e2eaf0' : '#18344a', muted = dark ? '#a5b9c9' : '#536c80';
  const missingColor = dark ? '#45505e' : '#d1d7df';
  const chartOption = useMemo(() => ({
    animation: false,
    tooltip: { trigger: 'item', renderMode: 'richText', confine: true, formatter: (param: { name: string }) => {
      const row = rowByCode.get(param.name);
      return row ? `${name(row.region)}\n${window?.metric === 'rate' ? t.rate : t.cases}: ${number(row.value)}\n${row.value === null ? reason(row) : row.annual.length > 1 ? `${t.annualPopulations}: ${row.annual.map(item => item.year).join(', ')}` : `${t.population}: ${number(row.population?.value, 0)}`}` : reasonLabels[lang].not_covered;
    } },
    visualMap: { type: 'continuous', show: false, min: 0, max: scaleValue(scale.maximum), calculable: false, inRange: { color: colors }, outOfRange: { color: missingColor }, seriesIndex: 0 },
    graphic: scaleGraphic(scale, colors, muted, dark ? '#8097a8' : '#94a7b6', missingColor, t.missing, value => value.toLocaleString(lang, { maximumFractionDigits: 0 })),
    series: [{ type: 'map', map: mapName, roam: true,
      ...(mapFocused && code === 'US' ? { boundingCoords: [[-126, 50], [-66, 24]] } : mapFocused && code === 'JP' ? { boundingCoords: [[128, 46], [146, 30]] } : {}), layoutCenter: ['54%', '43%'], layoutSize: '87%', selectedMode: false,
      itemStyle: { areaColor: dark ? '#354553' : '#e0e5e9', borderColor: dark ? '#10212e' : '#ffffff', borderWidth: .7 },
      emphasis: { label: { show: false }, itemStyle: { borderColor: '#e5a545', borderWidth: 2 } },
      data: rows.map(row => ({ name: row.region.code, value: row.value === null ? -1 : scaleValue(row.value),
        itemStyle: {
          ...(row.region.code === selected ? { borderColor: dark ? '#ffffff' : '#172d3c', borderWidth: 2 } : {}),
          ...(row.value === null ? { areaColor: dark ? '#45505e' : '#d1d7df', decal: row.reasons.includes('suppressed') ? { symbol: 'rect', dashArrayX: [1, 0], dashArrayY: [2, 5], rotation: -Math.PI / 4, color: dark ? '#8097a8' : '#94a7b6' } : undefined } : {}),
        } })),
    }],
  }), [rows, mapName, selected, scale, dark, lang, window?.metric, mapFocused, code]);
  const trend = useMemo(() => active && dataset && population && window && rangeValid ? trendFor(active.region, dataset, population, window) : [], [active, dataset, population, window, rangeValid]);
  const compareTrend = useMemo(() => active && dataset && population && compareWindow ? trendFor(active.region, dataset, population, compareWindow) : [], [active, dataset, population, compareWindow]);
  const trendOption = useMemo(() => ({
    animation: false, grid: { top: 40, left: 55, right: 20, bottom: 55 },
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true, formatter: (items: Array<{ seriesIndex: number; dataIndex: number; seriesName: string; value: number | null }>) => items.map(item => { const point = (item.seriesIndex === 0 ? trend : compareTrend)[item.dataIndex]; return point ? `${item.seriesName}: ${point.from} – ${point.to}\n${number(point.value)}` : ''; }).filter(Boolean).join('\n') }, legend: { textStyle: { color: muted } },
    xAxis: { type: 'category', data: Array.from({ length: Math.max(trend.length, compareTrend.length) }, (_, index) => trend[index]?.label || `${t.previous}: ${compareTrend[index]?.label || ''}`), axisLabel: { color: muted, rotate: trend.length > 14 ? 35 : 0 } },
    yAxis: { type: 'value', axisLabel: { color: muted }, splitLine: { lineStyle: { color: dark ? '#354858' : '#e1e7ed' } } },
    series: [{ type: 'line', name: t.current, data: trend.map(point => point.value), connectNulls: false, symbolSize: 5, itemStyle: { color: dark ? '#75c4cb' : '#236f83' }, lineStyle: { width: 2 } },
      ...(compareWindow ? [{ type: 'line', name: t.previous, data: compareTrend.map(point => point.value), connectNulls: false, symbolSize: 5, lineStyle: { width: 2, type: 'dashed' }, itemStyle: { color: dark ? '#e8bd77' : '#a36b24' } }] : [])], textStyle: { color: text },
  }), [trend, compareTrend, compareWindow, lang, dark]);

  if (status === 'absent') return null;
  if (status === 'loading') return <section id="regional-distribution" data-subdivision-country={code} className="figure-panel subdivision-map scroll-mt-24" aria-label={t.title}><p role="status">{t.loading}</p></section>;
  if (status === 'error' || !catalog) return <section id="regional-distribution" className="figure-panel subdivision-map scroll-mt-24"><h2 className="figure-title">{t.title}</h2><p role="status" className="figure-caption">{t.unavailable}</p></section>;
  const setRange = (patch: Partial<MapWindow>) => setWindow(previous => previous ? { ...previous, ...patch } : null);
  const share = () => {
    const url = new URL(globalThis.window.location.href);
    for (const key of ['mapYear', 'mapStart', 'mapEnd']) url.searchParams.delete(key);
    if (window) for (const [key, value] of Object.entries({ mapDisease: selectedDisease, mapFrom: window.from, mapTo: window.to, mapView: window.view, mapMetric: window.metric, mapRegion: selected, mapCompare: comparison, mapCompareFrom: customComparison.from, mapCompareTo: customComparison.to, mapTab: tab })) url.searchParams.set(key, String(value));
    url.hash = 'regional-distribution'; return url.href;
  };
  const months = monthNames(lang);
  const basisLabel = (basis: string) => populationBasis[basis]?.[lang === 'zh' ? 1 : lang === 'fr' ? 2 : 0] || basis;
  const bounds = dataset ? dateBounds(dataset) : { from: '2000-01-01', to: '2100-12-31' };
  const presetYear = Number(window?.from.slice(0, 4)) || Number(retainedRange.current?.from.slice(0, 4)) || (dataset ? Math.max(...dataset.years) : new Date().getUTCFullYear());
  const calendarYears = Array.from({ length: Number(bounds.to.slice(0, 4)) - Number(bounds.from.slice(0, 4)) + 1 }, (_, index) => Number(bounds.from.slice(0, 4)) + index).reverse();
  const expected = rows[0], comparedExpected = compareRows[0];
  const change = (row: RegionResult) => {
    const previous = compareByCode.get(row.region.code)?.value;
    return row.value !== null && previous != null && previous !== 0 ? `${number((row.value - previous) / previous * 100)}%` : '—';
  };
  const ui = workspaceLabels[lang];
  const diseaseLabel = dataset?.labels[lang] || dataset?.labels.en || selectedDisease;
  const metricLabel = window?.metric === 'rate' ? t.rate : t.cases;
  const caseSource = ({ AU: 'NINDSS', BR: 'SINAN', CN: 'Provincial statutory reports', JP: 'IDWR', SE: 'FHM SmiNet', US: 'CDC NNDSS' } as Record<string, string>)[code];
  const populationSources = [...new Set(rows.flatMap(row => row.annual.flatMap(item => item.population ? [item.population.source] : [])))];
  const selectedSource = dataset?.regions[selected];
  const tabs = ['map', 'trend', 'table'] as const;
  const zoomLabels = lang === 'zh'
    ? { group: '地图缩放', in: '放大地图', out: '缩小地图', reset: '重置地图' }
    : lang === 'fr'
      ? { group: 'Zoom de la carte', in: 'Agrandir la carte', out: 'Réduire la carte', reset: 'Réinitialiser la carte' }
      : { group: 'Map zoom', in: 'Zoom in', out: 'Zoom out', reset: 'Reset map' };
  const zoom = (factor: number) => mapRef.current?.getEchartsInstance?.()?.dispatchAction({ type: 'geoRoam', componentType: 'series', seriesIndex: 0, zoom: factor });
  const resetZoom = () => mapRef.current?.getEchartsInstance?.()?.setOption(chartOption, { notMerge: true });
  const detail = active && window && <div className="subdivision-map-detail" aria-live="polite">
    <h3>{name(active.region)}{active.provisional && <span className="subdivision-map-badge">{t.provisional}</span>}{active.unverified && <span className="subdivision-map-badge">{t.unverified}</span>}</h3>
    <div className="subdivision-map-value">{number(active.value)}<span>{metricLabel}</span></div>
    {active.value === null && <p className="surveillance-note">{reason(active)}</p>}
    <dl>
      <div><dt>{t.cases}</dt><dd>{number(active.cases, 0)}</dd></div>
      <div><dt>{ui.population}</dt><dd>{active.annual.length > 1 ? active.annual.map(item => item.year).join(' / ') : number(active.population?.value, 0)}{active.population ? ` (${active.population.year})` : ''}</dd></div>
      <div><dt>{t.observed}</dt><dd>{active.observed}/{active.expected}</dd></div>
    </dl>
    <p className="subdivision-map-source">{active.population ? <a href={active.population.source_url} target="_blank" rel="noopener noreferrer">{active.population.source} ↗</a> : ui.methods}{window.metric === 'rate' && <> · {ui.shortRate}</>}</p>
  </div>;
  return <section id="regional-distribution" className="figure-panel subdivision-map scroll-mt-24" data-subdivision-country={code}>
    <div ref={shell} className={`surveillance-explorer panel-fullscreen subdivision-map-workspace ${fullscreen ? 'is-fullscreen' : ''}`}
      data-chart-title={`${code} · ${t.title} · ${diseaseLabel} · ${metricLabel}${tab === 'trend' && active ? ` · ${name(active.region)}` : ''}`}
      data-disease-names={dataset ? JSON.stringify(dataset.labels) : undefined}
      data-source-label={`${caseSource} · ${populationSources.join(' / ')} · ${catalog.boundary.source} (${catalog.boundary.license})`}
      data-source-coverage={window ? `${window.from} – ${window.to}${tab === 'trend' && compareWindow ? ` · ${t.previous}: ${compareWindow.from} – ${compareWindow.to}` : ''}${window.metric === 'rate' ? ` · ${ui.shortRate}` : ''}` : undefined}>
      <h2 className="figure-title">{t.title}</h2>
      <div className="subdivision-map-toolbar">
        <div className="subdivision-map-controls">
          <label htmlFor={`${uid}-disease`}>{t.disease}<select id={`${uid}-disease`} aria-label={t.disease} value={selectedDisease} onChange={event => setSelectedDisease(event.target.value)}>
            {diseaseId && !selectedDisease && <option value="">—</option>}
            {catalog.diseases.map(disease => <option key={disease.id} value={disease.id}>{disease.labels[lang] || disease.labels.en}</option>)}
          </select></label>
          {window && dataset && <>
            <label htmlFor={`${uid}-metric`}>{t.metric}<select id={`${uid}-metric`} aria-label={t.metric} value={window.metric} onChange={event => setRange({ metric: event.target.value as MapWindow['metric'] })}><option value="rate">{t.rate}</option><option value="cases">{t.cases}</option></select></label>
            <label htmlFor={`${uid}-year`}>{t.year}<select id={`${uid}-year`} aria-label={t.year} value={presetYear} onChange={event => { const year = Number(event.target.value); setWindow(validWindow(window) ? shiftWindowYear(window, year - Number(window.from.slice(0, 4))) : presetWindow(window, year, 'year')); }}>{calendarYears.map(year => <option key={year} value={year}>{year}</option>)}</select></label>
            <label htmlFor={`${uid}-view`}>{t.view}<select id={`${uid}-view`} aria-label={t.view} value={window.view} onChange={event => setRange({ view: event.target.value as Cadence })}>{(Object.keys(cadenceRank) as Cadence[]).map(cadence => <option key={cadence} value={cadence} disabled={cadenceRank[cadence] < cadenceRank[dataset.cadence]}>{cadenceLabels[lang][cadence]}</option>)}</select></label>
          </>}
        </div>
        <div className="subdivision-map-actions">
          <ChartResultActions lang={lang} shellRef={shell} onShare={share}
            pngFilename={`${code}-${selectedDisease}-${window?.from}-${window?.to}-${tab}.png`}
            pngSelector={tab === 'trend' ? '.subdivision-map-trend' : '.subdivision-map-plot'}
            pngDisabled={!rangeValid || !dataset || tab === 'table'}
            dataHref={downloadCenterHref(lang, { country: selected || code, disease: selectedDisease })} />
        </div>
      </div>
      {!selectedDisease ? <p className="surveillance-note" role="status">{t.noDisease}</p> : !dataset || !window ? <p role="status">{failed ? t.unavailable : t.loading}</p> : <>
        <div className="subdivision-map-range surveillance-time">
          <label htmlFor={`${uid}-from`}><span className="sr-only">{t.from}</span><input type="date" id={`${uid}-from`} aria-label={t.from} min={bounds.from} max={bounds.to} value={window.from} onChange={event => setRange({ from: event.target.value })} /></label>
          <span aria-hidden="true">–</span>
          <label htmlFor={`${uid}-to`}><span className="sr-only">{t.to}</span><input type="date" id={`${uid}-to`} aria-label={t.to} min={bounds.from} max={bounds.to} value={window.to} onChange={event => setRange({ to: event.target.value })} /></label>
          {cadenceRank[dataset.cadence] <= 1 && <label><span className="sr-only">{t.monthPreset}</span><select aria-label={t.monthPreset} value="" onChange={event => { if (event.target.value) setWindow(presetWindow(window, presetYear, 'month', Number(event.target.value))); }}><option value="">{t.monthPreset}</option>{months.map((month, index) => <option key={month} value={index + 1}>{month}</option>)}</select></label>}
          {cadenceRank[dataset.cadence] <= 2 && <label><span className="sr-only">{t.quarterPreset}</span><select aria-label={t.quarterPreset} value="" onChange={event => { if (event.target.value) setWindow(presetWindow(window, presetYear, 'quarter', Number(event.target.value))); }}><option value="">{t.quarterPreset}</option>{[1, 2, 3, 4].map(quarter => <option key={quarter} value={quarter}>Q{quarter}</option>)}</select></label>}
          <button type="button" onClick={() => setWindow(presetWindow(window, presetYear, 'year'))}>{t.yearPreset}</button>
          <label htmlFor={`${uid}-comparison`}><span className="sr-only">{t.compare}</span><select id={`${uid}-comparison`} aria-label={t.compare} value={comparison} onChange={event => setComparison(event.target.value as Comparison)}>{(['none', 'previous_year', 'previous_period', 'custom'] as Comparison[]).map(mode => <option key={mode} value={mode}>{t[mode]}</option>)}</select></label>
          {comparison === 'custom' && <>
            <label>{t.compareFrom}<input aria-label={t.compareFrom} type="date" value={customComparison.from} onChange={event => setCustomComparison(previous => ({ ...previous, from: event.target.value }))} /></label>
            <label>{t.compareTo}<input aria-label={t.compareTo} type="date" value={customComparison.to} onChange={event => setCustomComparison(previous => ({ ...previous, to: event.target.value }))} /></label>
          </>}
        </div>
        {(!rangeValid || (comparison === 'custom' && !compareWindow)) && <p role="alert" className="surveillance-status">{t.invalid}</p>}
        <div className="subdivision-map-views">
          <div className="surveillance-tabs" role="tablist" aria-label={t.title} onKeyDown={event => {
            const index = tabs.indexOf(tab);
            const next = event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length] : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length] : event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[2] : null;
            if (!next) return;
            event.preventDefault(); setTab(next); document.getElementById(`${uid}-${next}`)?.focus();
          }}>
            {tabs.map(value => <button key={value} id={`${uid}-${value}`} role="tab" type="button" aria-selected={tab === value} aria-controls={`${uid}-panel`} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)}>{ui[value]}</button>)}
          </div>
          <label className="chart-measure">{t.area}<select aria-label={t.selected} value={selected} onChange={event => setSelected(event.target.value)}>{catalog.regions.map(region => <option key={region.code} value={region.code}>{name(region)}</option>)}</select></label>
          {tab === 'map' && (code === 'US' || code === 'JP') && <button type="button" aria-pressed={mapFocused} onClick={() => setMapFocused(!mapFocused)}>{mapFocused ? t.reset : t.focus}</button>}
        </div>
        <p className="subdivision-map-status" aria-live="polite">{rows.filter(row => row.value !== null).length}/{catalog.expected_regions} {ui.available} · {cadenceLabels[lang][dataset.cadence]}{compareWindow && expected?.expected !== comparedExpected?.expected && <> · {ui.periodDifference}</>}</p>
        <div role="tabpanel" id={`${uid}-panel`} aria-labelledby={`${uid}-${tab}`}>
          {tab === 'map' && <div className="subdivision-map-content">
            <div className="subdivision-map-plot"><div className="subdivision-map-canvas" role="img" aria-label={`${t.title} · ${metricLabel}. ${scale.ticks.map(tick => tick.value.toLocaleString(lang, { maximumFractionDigits: 0 })).join('; ')}; ${t.missing}`}><EChartsReact ref={mapRef} echarts={echarts} option={chartOption} notMerge style={{ height: '100%', width: '100%', position: 'relative', zIndex: 1 }} onEvents={{ click: (event: { name: string }) => { if (rowByCode.has(event.name)) setSelected(event.name); } }} /><div className="subdivision-map-zoom" role="group" aria-label={zoomLabels.group}><button type="button" onClick={() => zoom(1.25)} aria-label={zoomLabels.in}>+</button><button type="button" onClick={() => zoom(.8)} aria-label={zoomLabels.out}>−</button><button type="button" onClick={resetZoom} aria-label={zoomLabels.reset}>↺</button></div></div></div>
            <RegionList rows={rows} previous={compareByCode} comparison={!!compareWindow} lang={lang} selected={selected} onSelect={setSelected} metricLabel={metricLabel} number={number} reason={reason}>{detail}</RegionList>
          </div>}
          {tab === 'trend' && active && <>
            <div className="subdivision-map-trend"><EChartsReact echarts={echarts} option={trendOption} notMerge style={{ height: fullscreen ? '55vh' : 300, minHeight: 240, width: '100%' }} /></div>
            {detail}
          </>}
          {tab === 'table' && <div className="subdivision-map-table" role="region" aria-label={t.area}>
            <table><thead><tr><th scope="col">{t.area}</th><th scope="col">{metricLabel}</th>{compareWindow && <><th scope="col">{t.previous}</th><th scope="col">{t.change}</th></>}</tr></thead><tbody>
              {[...rows].sort((a, b) => (b.value ?? -1) - (a.value ?? -1)).map(row => <tr key={row.region.code} data-selected={selected === row.region.code || undefined}><th scope="row"><button type="button" aria-pressed={selected === row.region.code} onClick={() => { setSelected(row.region.code); setTab('map'); }}>{name(row.region)}</button></th><td>{row.value === null ? <span className="subdivision-map-unavailable">{reason(row)}</span> : number(row.value)}{row.provisional && <span className="sr-only"> {t.provisional}</span>}{row.unverified && <span className="sr-only"> {t.unverified}</span>}</td>{compareWindow && <><td>{number(compareByCode.get(row.region.code)?.value)}</td><td>{change(row)}</td></>}</tr>)}
            </tbody></table>
          </div>}
        </div>
        <details className="subdivision-map-methods surveillance-methods">
          <summary>{ui.methods}</summary>
          <p>{dataset.case_basis[lang] || dataset.case_basis.en}</p>{active?.unverified && <p>{t.unverified}: {lang === 'zh' ? '数值来自尚未完成核验的原始来源记录。' : lang === 'fr' ? 'Valeur issue d’une observation source non vérifiée.' : 'The value comes from an unverified source observation.'}</p>}
          <p>{t.periodRule}</p>
          {expected?.from && <p>{t.actual}: {expected.from} – {expected.to} ({expected.expected}){comparedExpected?.from && <> · {t.previous}: {comparedExpected.from} – {comparedExpected.to} ({comparedExpected.expected})</>}</p>}
          {compareWindow && expected?.expected !== comparedExpected?.expected && <p>{t.equalDuration}</p>}
          <p>{t.formula} {t.scale}</p>
          <p>{lang === 'zh' ? '颜色使用连续对数色阶，图例刻度参考 R pretty 取整数，按实际对应数值定位。原始病例数和报告率不取整，缺失值单独标记。' : lang === 'fr' ? 'Échelle logarithmique continue avec graduations entières inspirées de R pretty, positionnées selon leur valeur réelle. Les cas et taux sources ne sont pas arrondis ; les données manquantes sont distinctes.' : 'Continuous logarithmic colours use integer ticks inspired by R pretty, positioned at their actual values. Source counts and rates are not rounded; missing values are marked separately.'}</p>
          {compareWindow && <p>{t.alignment}</p>}
          {dataset.cadence === 'weekly' && window.view !== 'weekly' && <p>{t.weeklyGrouping}</p>}
          {active?.reasons.includes('missing_population') && <p>{t.noPopulation}</p>}
          {active && <div className="subdivision-map-populations"><h4>{t.annualPopulations}</h4><table><thead><tr><th>{t.year}</th><th>{t.cases}</th><th>{t.population}</th><th>{t.source}</th><th>{t.reference}</th><th>{t.basis}</th><th>{t.rate}</th></tr></thead><tbody>{active.annual.map(item => <tr key={item.year}><th scope="row">{item.year}</th><td>{number(item.cases, 0)}</td><td>{number(item.population?.value, 0)}</td><td>{item.population ? <a href={item.population.source_url} target="_blank" rel="noopener noreferrer">{item.population.source} ↗</a> : '—'}</td><td>{item.population?.reference_date || '—'}</td><td>{item.population ? basisLabel(item.population.basis) : '—'}</td><td>{number(item.rate)}</td></tr>)}</tbody></table></div>}
          {code === 'JP' && <p>{t.populationPrecision}</p>}
          {selectedSource?.semantics.source_labels?.length ? <p>{selectedSource.semantics.source_labels.join(' · ')}{selectedSource.source_url && <> · <a href={selectedSource.source_url} target="_blank" rel="noopener noreferrer">{t.source} ↗</a></>}</p> : null}
          <p>{t.boundary}: <a href={catalog.boundary.source_url} target="_blank" rel="noopener noreferrer">{catalog.boundary.source}</a> · <a href={catalog.boundary.license_url} target="_blank" rel="noopener noreferrer">{catalog.boundary.license}</a>. {t.historical}</p>
        </details>
      </>}
    </div>
  </section>;
}
