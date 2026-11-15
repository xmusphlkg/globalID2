import GIDSLoader from '../GIDSLoader';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import SurveillanceSources from './SurveillanceSources';
import SurveillanceDownloads from './SurveillanceDownloads';
import { cadence, diseaseLabel, number, coverage, type Language, type Summary } from '../../lib/surveillance-presentation.ts';
import { nationalRows, subdivisionRows, officialSourceCount } from '../../lib/surveillance-coverage';

type Mode = 'country-coverage' | 'country-indicators' | 'country-diseases' | 'country-disease-indicators' | 'downloads' | 'sources' | 'country-card' | 'coverage-caption' | 'global-coverage' | 'directory-indicators';
export default function SurveillanceSummary({ dataUrl, mode, language = 'en', code, diseaseId, routes = {}, reportCount }: { dataUrl: string; mode: Mode; language?: Language; code?: string; diseaseId?: string; routes?: Record<string, string>; reportCount?: number }) {
  const { data: payload, version, error, retry } = useSurveillanceSummary(dataUrl);
  const data = payload && mode === 'country-card' ? (payload.countries ?? []).find((row: Summary) => String(row.code).toUpperCase() === code?.toUpperCase()) : payload;
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  const prefix = language === 'en' ? '' : `/${language}`;
  if (mode === 'downloads') return <div className="space-y-4" data-surveillance-version={version}>
    <SurveillanceDownloads dataUrl={dataUrl} language={language} />
    {data && <SurveillanceSources data={data} language={language} />}
  </div>;
  if (!data) return <div className="text-sm text-[rgb(var(--text-muted))]" role="status">
    {error ? <>{pick('Current data is unavailable.', '当前数据暂不可用。', 'Les données actuelles sont indisponibles.')} {mode !== 'country-card' && <button type="button" className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button>}</>
      : <GIDSLoader language={language} compact />}
  </div>;
  const timestamp = data.generated_at;
  const revision = <p className="mt-3 text-xs text-[rgb(var(--text-muted))]" data-surveillance-version={version}>
    {timestamp ? `${pick('Data updated', '数据更新于', 'Données mises à jour')} ${String(timestamp).slice(0, 10)}` : ''}
  </p>;
  if (mode === 'country-card') return <div data-surveillance-version={version}><div className="grid grid-cols-3 gap-2 border-t border-[rgb(var(--border))] pt-3 text-center text-sm">
    {[[pick('Cases', '病例', 'Cas'), data.total_cases], [pick('Deaths', '死亡', 'Décès'), data.total_deaths], [pick('Diseases', '疾病', 'Maladies'), data.disease_count]].map(([name, value]) => <div key={String(name)}><strong className="block tabular-nums">{number(value, language)}</strong><span className="text-xs text-[rgb(var(--text-muted))]">{name}</span></div>)}
  </div><p className="mt-2 text-xs text-[rgb(var(--text-muted))]">{coverage(data)}</p></div>;
  const national = nationalRows(data);
  const subdivisions = subdivisionRows(data);
  const sourceCount = officialSourceCount(data);
  if (mode === 'coverage-caption') return <p className="figure-caption">{number(national.length, language)} {pick('areas with data', '个区域有监测数据', 'zones avec des données')} · {pick('Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '—').slice(0, 10)}</p>;
  if (mode === 'global-coverage') {
    const starts = national.map((row: Summary) => row.date_range?.start).filter(Boolean).sort();
    const ends = national.map((row: Summary) => row.date_range?.end).filter(Boolean).sort();
    return <p className="figure-caption" data-surveillance-version={version}>{pick('Coverage across top-level area series', '上级区域序列时间覆盖', 'Couverture des séries des zones de niveau supérieur')} {starts[0] ?? '—'} → {ends.at(-1) ?? '—'}</p>;
  }
  if (mode === 'country-coverage') return <div className="academic-meta-grid xl:grid-cols-2">
    <div className="academic-meta-item"><div className="academic-meta-label">{pick('Coverage window', '覆盖区间', 'Période couverte')}</div><div className="academic-meta-value text-sm">{coverage(data)}</div></div>
    <div className="academic-meta-item"><div className="academic-meta-label">{pick('Tracked diseases', '追踪疾病数', 'Maladies suivies')}</div><div className="academic-meta-value font-serif">{number(data.disease_count, language)}</div></div>
    {revision}
  </div>;
  if (mode === 'country-indicators' || mode === 'directory-indicators') {
    const values = mode === 'country-indicators' ? [
      [pick('Total cases', '累计病例', 'Cas cumulés'), data.total_cases],
      [pick('Total deaths', '累计死亡', 'Décès cumulés'), data.total_deaths],
      [pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), data.disease_count],
      ...(reportCount !== undefined ? [[pick('Published reports', '已发布报告', 'Rapports publiés'), reportCount]] : []),
    ] : [
      [pick('Top-level areas', '上级区域', 'Zones de niveau supérieur'), national.length],
      [pick('Subdivision datasets', '下级行政单位数据集', 'Jeux de données des subdivisions'), subdivisions.length],
      [pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), data.total_diseases ?? data.disease_count],
      [pick('Official sources', '官方来源', 'Sources officielles'), sourceCount],
      ...(reportCount !== undefined ? [[pick('Published reports', '已发布报告', 'Rapports publiés'), reportCount]] : []),
    ];
    return <div data-surveillance-version={version}>
      <dl className="site-stat-grid grid grid-cols-2 sm:grid-cols-4 gap-px border">
        {values.map(([name, value]) => <div className="site-stat-item px-4 py-5 text-center" key={String(name)}><dt className="academic-kicker mb-2">{name}</dt><dd className="font-semibold tabular-nums">{number(value, language)}</dd></div>)}
      </dl>{revision}
      {mode !== 'country-indicators' && <p className="figure-caption mt-2">{pick('Top-level areas and subdivisions are counted separately; their totals are not added together.', '上级区域与下级行政单位分别统计，子级数据不叠加到上级总量。', 'Les zones de niveau supérieur et leurs subdivisions sont comptées séparément, sans additionner leurs totaux.')}</p>}
    </div>;
  }
  if (mode === 'country-diseases') return <div><ul className="mt-4 flex flex-wrap gap-2">
    {[...(data.diseases ?? [])].sort((a, b) => Number(b.total_cases ?? 0) - Number(a.total_cases ?? 0)).slice(0, 16).map((row: Summary) => <li key={String(row.disease_id ?? row.id)}><a href={routes[row.slug] || `${prefix}/diseases/${encodeURIComponent(row.slug || row.disease_id || row.id)}/`} className="site-action-secondary inline-flex px-3 py-1.5 text-sm border">{diseaseLabel(row, language)}</a></li>)}
  </ul>{!(data.diseases?.length > 0) && <p className="figure-caption">{pick('No mapped disease series are available.', '暂无已映射的疾病序列。', 'Aucune série de maladie associée.')}</p>}</div>;
  if (mode === 'country-disease-indicators') {
    const series = (data.diseases ?? []).find((entry: Summary) => String(entry.disease_id ?? entry.id).toUpperCase() === diseaseId?.toUpperCase());
    const start = series?.latest_period_start;
    const end = series?.latest_period_end_exclusive ? new Date(`${series.latest_period_end_exclusive}T00:00:00Z`) : null;
    if (end && Number.isFinite(end.getTime())) end.setUTCDate(end.getUTCDate() - 1);
    const lastPeriod = start ? end && Number.isFinite(end.getTime()) && end.toISOString().slice(0, 10) !== start ? `${start} → ${end.toISOString().slice(0, 10)}` : start : '—';
    return <div className="academic-meta-grid mt-6" data-surveillance-version={version}>{[
      [pick('Coverage', '覆盖区间', 'Période couverte'), series ? coverage(series) : '—'],
      [pick('Reporting granularity', '报告粒度', 'Granularité des rapports'), cadence(series?.period_granularity, language)],
      [pick('Latest reported value', '最新报告值', 'Dernière valeur publiée'), number(series?.latest_cases, language)],
      [pick('Latest reporting period', '最新报告期', 'Dernière période déclarée'), lastPeriod],
    ].map(([name, value]) => <div className="academic-meta-item" key={name}><div className="academic-meta-label">{name}</div><div className="academic-meta-value text-sm">{value}</div></div>)}</div>;
  }
  return <div className="space-y-4" data-surveillance-version={version}>
    <SurveillanceSources data={data} language={language} />
    {revision}
  </div>;
}
