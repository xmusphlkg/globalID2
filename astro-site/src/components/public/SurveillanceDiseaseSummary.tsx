import { useSurveillanceSummary } from './useSurveillanceSummary';
import SurveillanceSources from './SurveillanceSources';
import SurveillanceDownloads from './SurveillanceDownloads';
import { getFlagAssetPath } from '../../lib/country-flag';
import { countryLabel, number, coverage, pick as translate, type Language, type Summary } from '../../lib/surveillance-presentation';

export default function SurveillanceDiseaseSummary({ dataUrl, mode, language, routes = {} }: { dataUrl: string; mode: 'disease-coverage' | 'disease-countries' | 'downloads'; language: Language; routes?: Record<string, string> }) {
  const { data, version, error, retry } = useSurveillanceSummary(dataUrl);
  const pick = (en: string, zh: string, fr: string) => translate(language, en, zh, fr);
  const prefix = language === 'en' ? '' : `/${language}`;
  if (mode === 'downloads') return <div className="space-y-4" data-surveillance-version={version}>
    <SurveillanceDownloads dataUrl={dataUrl} language={language} />
    {data && <SurveillanceSources data={data} language={language} />}
  </div>;
  if (!data) return <p role="status">{error ? <>{pick('Current data is unavailable.', '当前数据暂不可用。', 'Les données actuelles sont indisponibles.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></> : pick('Loading current data…', '正在加载最新数据…', 'Chargement des données actuelles…')}</p>;
  const revision = <p className="figure-caption mt-3" data-surveillance-version={version}>{pick('Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '—').slice(0, 10)}</p>;
  if (mode === 'disease-coverage') return <div className="academic-meta-grid xl:grid-cols-2">
    {[[pick('Coverage window', '覆盖区间', 'Période couverte'), coverage(data)], [pick('Reporting countries', '报告国家数', 'Pays déclarants'), number(data.country_count, language)]].map(([name, value]) => <div className="academic-meta-item" key={name}><div className="academic-meta-label">{name}</div><div className="academic-meta-value text-sm">{value}</div></div>)}{revision}
  </div>;
  if (mode === 'disease-countries') return <div><p className="figure-caption">{number(data.country_count, language)} {pick('reporting countries', '个报告国家', 'pays déclarants')}</p><ul className="grid gap-2 mt-4 sm:grid-cols-2 lg:grid-cols-4">
    {(data.reporting_countries ?? []).map((row: Summary) => <li key={row.code}><a href={routes[String(row.code).toUpperCase()] || `${prefix}/countries/${encodeURIComponent(String(row.code).toLowerCase())}/`} className="site-action-secondary flex items-center gap-2 px-3 py-2 text-sm border"><img src={getFlagAssetPath(row.code) ?? undefined} alt="" width="28" height="20" loading="lazy" /><span>{countryLabel(row, language)}</span><span className="text-xs text-[rgb(var(--text-muted))]">{row.code}</span></a></li>)}
  </ul></div>;
  return null;
}
