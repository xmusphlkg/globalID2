import { useEffect, useMemo, useState } from 'react';
import { loadDiseaseDataset, type DiseaseDatasetSeriesEntry, type DiseaseDatasetMonthlyData } from './diseaseDataset';
import DiseaseSeasonalityHeatmap from './DiseaseSeasonalityHeatmap';

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
  const [series, setSeries] = useState<Record<string, DiseaseDatasetSeriesEntry>>({});
  const [country, setCountry] = useState(initialCountry ?? '');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    loadDiseaseDataset(dataUrl).then(data => { if (!cancelled) { setSeries(data.country_series ?? {}); setLoading(false); } }).catch(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [dataUrl]);
  const options = useMemo(() => Object.entries(series).filter(([code, entry]) => /^[A-Z]{2}$/.test(code) && monthlySeries(entry)).sort((a, b) => a[1].name_en.localeCompare(b[1].name_en)), [series]);
  const active = options.some(([code]) => code === country) ? country : options[0]?.[0];
  const data = active ? monthlySeries(series[active]) : null;
  if (loading) return <div className="chart-loading-panel" role="status" aria-busy="true" style={{ height: 260 }} />;
  if (!data) return <p className="disease-trend-empty">{language === 'zh' ? '暂无可用的国家级月度序列。' : language === 'fr' ? 'Aucune série mensuelle nationale disponible.' : 'No national monthly series is available.'}</p>;
  return <div><label className="disease-seasonality-select">{language === 'zh' ? '国家' : language === 'fr' ? 'Pays' : 'Country'} <select value={active} onChange={event => setCountry(event.target.value)}>{options.map(([code, entry]) => <option key={code} value={code}>{language === 'zh' ? entry.name_zh : language === 'fr' ? entry.name_fr : entry.name_en} ({code})</option>)}</select></label><DiseaseSeasonalityHeatmap key={active} data={data} language={language} filename={`gids-${active.toLowerCase()}-seasonality`} /></div>;
}
