import GIDSLoader from '../GIDSLoader';
import { useEffect, useMemo, useState } from 'react';
import { loadDiseaseDataset, type DiseaseDatasetSeriesEntry, type DiseaseDatasetMonthlyData } from './diseaseDataset';
import DiseaseSeasonalityHeatmap from './DiseaseSeasonalityHeatmap';
import { useSurveillanceSummary } from '../public/useSurveillanceSummary';
import { countryLabel } from '../../lib/surveillance-presentation';
import { isSubdivision } from '../../lib/surveillance-coverage';

type Language = 'en' | 'zh' | 'fr';
function monthlySeries(entry: DiseaseDatasetSeriesEntry): DiseaseDatasetMonthlyData | null {
  const rows = entry.dates.map((date, index) => ({
    month: date.slice(0, 7),
    period: entry.point_granularities?.[index] ?? entry.period_granularity,
    cases: entry.cases?.[index] ?? null,
    deaths: entry.deaths?.[index] ?? null,
  })).filter(row => /^\d{4}-\d{2}$/.test(row.month) && ['month', 'monthly'].includes(String(row.period).toLowerCase()));
  if (rows.filter(row => row.cases != null || row.deaths != null).length < 2 || new Set(rows.map(row => row.month)).size !== rows.length) return null;
  rows.sort((a, b) => a.month.localeCompare(b.month));
  return { months: rows.map(row => row.month), cases: rows.map(row => row.cases), deaths: rows.map(row => row.deaths) };
}

export default function CountrySeasonalityPicker({ dataUrl, language = 'en', initialCountry }: { dataUrl: string; language?: Language; initialCountry?: string }) {
  const publication = useSurveillanceSummary('/site-data/meta.json');
  const geography = useMemo(() => new Map<string, Record<string, any>>((publication.data?.countries ?? []).map((row: Record<string, any>) => [row.code, row])), [publication.data]);
  const [series, setSeries] = useState<Record<string, DiseaseDatasetSeriesEntry>>({});
  const [country, setCountry] = useState(initialCountry ?? '');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    loadDiseaseDataset(dataUrl).then(data => { if (!cancelled) { setSeries(data.country_series ?? {}); setLoading(false); } }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [dataUrl]);
  const options = useMemo(() => Object.entries(series).filter(([, entry]) => monthlySeries(entry))
    .map(([code]) => ({ code, name: countryLabel(geography.get(code) ?? { code }, language), subdivision: isSubdivision(geography.get(code) ?? { code }) }))
    .sort((a, b) => Number(a.subdivision) - Number(b.subdivision) || a.name.localeCompare(b.name, language)), [series, geography, language]);
  const active = options.some(option => option.code === country) ? country : options[0]?.code;
  const data = active ? monthlySeries(series[active]) : null;
  if (loading) return <div className="chart-loading-panel" style={{ height: 260 }}><GIDSLoader language={language} /></div>;
  if (!data) return <p className="disease-trend-empty">{language === 'zh' ? '暂无可用的区域月度序列。' : language === 'fr' ? 'Aucune série mensuelle disponible pour les zones géographiques.' : 'No monthly series for geographic areas is available.'}</p>;
  return <div><div className="disease-seasonality-toolbar"><label className="disease-seasonality-select">{language === 'zh' ? '报告区域' : language === 'fr' ? 'Zone déclarante' : 'Reporting area'} <select value={active} onChange={event => setCountry(event.target.value)}>{[false, true].map(subdivision => <optgroup key={String(subdivision)} label={subdivision ? language === 'zh' ? '下一级行政单位' : language === 'fr' ? 'Unités administratives de niveau inférieur' : 'Lower-level administrative units' : language === 'zh' ? '上级区域' : language === 'fr' ? 'Zones de niveau supérieur' : 'Top-level areas'}>{options.filter(option => option.subdivision === subdivision).map(option => <option key={option.code} value={option.code}>{option.name} ({option.code})</option>)}</optgroup>)}</select></label><span>{data.months[0]} — {data.months[data.months.length - 1]}</span></div><DiseaseSeasonalityHeatmap key={active} data={data} dataUrl={`/site-data/countries/${active.toLowerCase()}.json`} language={language} filename={`gids-${active.toLowerCase()}-seasonality`} /></div>;
}
