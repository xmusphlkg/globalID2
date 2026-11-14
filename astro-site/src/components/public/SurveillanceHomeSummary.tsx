import GIDSLoader from '../GIDSLoader';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import { getFlagAssetPath } from '../../lib/country-flag';
import { cadence, countryLabel, number, coverage, pick as translate, type Language, type Summary } from '../../lib/surveillance-presentation';
import './home-summary.css';
import { nationalRows, subdivisionRows, officialSourceCount } from '../../lib/surveillance-coverage';

export default function SurveillanceHomeSummary({ dataUrl, mode, language, reportCount, metric }: { dataUrl: string; mode: 'home-indicators' | 'home-count' | 'recent-countries' | 'country-links'; language: Language; reportCount?: number; metric?: 'countries' | 'diseases' | 'sources' }) {
  const { data, version, error, retry } = useSurveillanceSummary(dataUrl);
  const pick = (en: string, zh: string, fr: string) => translate(language, en, zh, fr);
  const prefix = language === 'en' ? '' : `/${language}`;
  const metricLabels = [pick('Top-level areas', '上级区域', 'Zones de niveau supérieur'), pick('Subdivision datasets', '下级行政单位数据集', 'Jeux de données des subdivisions'), pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), pick('Official sources', '官方来源', 'Sources officielles'), ...(reportCount !== undefined ? [pick('Published reports', '已发布报告', 'Rapports publiés')] : [])];
  if (!data) {
    if (mode === 'home-count') return <span aria-label={pick('Not loaded', '尚未加载', 'Non chargé')}>—</span>;
    if (error) return <p role="status">{pick('Current data is unavailable.', '当前数据暂时无法读取。', 'Les données actuelles sont indisponibles.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></p>;
    if (mode === 'home-indicators') return <div className="home-summary" aria-busy="true">
      <dl className="asset-grid">{metricLabels.map((label, index) => <div key={label}><dt className="academic-kicker mb-2">{label}</dt><dd className="font-semibold tabular-nums">{index === 4 ? number(reportCount, language) : '—'}</dd></div>)}</dl>
      <p className="home-summary-status"><GIDSLoader language={language} compact label={pick('Updating statistics…', '正在更新统计…', 'Actualisation des statistiques…')} /></p>
      <p className="figure-caption mt-2">{pick('Top-level areas and subdivisions are counted separately; their totals are not added together.', '上级区域与下级行政单位分别统计，子级数据不叠加到上级总量。', 'Les zones de niveau supérieur et leurs subdivisions sont comptées séparément, sans additionner leurs totaux.')}</p>
    </div>;
    return <div className={`home-list-placeholder home-list-placeholder--${mode}`} aria-busy="true"><GIDSLoader language={language} label={pick('Loading areas…', '正在读取区域概览…', 'Chargement des zones…')} /></div>;
  }
  const national: Summary[] = nationalRows(data), subdivisions = subdivisionRows(data), sourceCount = officialSourceCount(data);
  const nationalCount = data.national_count ?? national.length, subdivisionCount = data.subdivision_count ?? subdivisions.length, sources = data.official_source_count ?? sourceCount;
  if (mode === 'home-count') return <span data-surveillance-version={version}>{number(metric === 'countries' ? nationalCount : metric === 'sources' ? sources : data.total_diseases, language)}</span>;
  if (mode === 'country-links' || mode === 'recent-countries') {
    const rows: Summary[] = (mode === 'recent-countries' ? data.recent_countries : data.featured_countries) ?? [...national].sort((a, b) => mode === 'recent-countries'
      ? String(b.date_range?.end ?? '').localeCompare(String(a.date_range?.end ?? ''))
      : Number(b.record_count ?? 0) - Number(a.record_count ?? 0)).slice(0, mode === 'recent-countries' ? 3 : 6);
    return <div className={mode === 'recent-countries' ? 'home-recent' : 'home-countries'} data-surveillance-version={version}>{rows.map(row => <a key={row.code} href={`${prefix}/countries/${String(row.code).toLowerCase()}/`}>
      <img src={getFlagAssetPath(row.code) ?? undefined} alt="" width="32" height="24" loading="lazy" />
      <span><strong>{countryLabel(row, language)}</strong><small>{mode === 'recent-countries' ? `${pick('Reporting cadence', '报告频率', 'Fréquence de déclaration')}: ${cadence(row.source_info?.sources?.[0]?.cadence, language)}` : `${number(row.disease_count, language)} ${pick('diseases', '种疾病', 'maladies')} · ${coverage(row)}`}</small>
      {mode === 'country-links' && <small>{pick('Reported cases', '报告病例', 'Cas déclarés')}: {number(row.total_cases, language)}</small>}</span>
      {mode === 'recent-countries' && <time dateTime={row.date_range?.end}>{row.date_range?.end || '—'}</time>}
    </a>)}</div>;
  }

  return <div className="home-summary" data-surveillance-version={version}>
    <dl className="asset-grid">{[
      [metricLabels[0], nationalCount],
      [metricLabels[1], subdivisionCount],
      [pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), data.total_diseases],
      [metricLabels[3], sources],
      ...(reportCount !== undefined ? [[pick('Published reports', '已发布报告', 'Rapports publiés'), reportCount]] : []),
    ].map(([name, value]) => <div key={String(name)}><dt className="academic-kicker mb-2">{name}</dt><dd className="font-semibold tabular-nums">{number(value, language)}</dd></div>)}</dl>
    <p className="figure-caption home-summary-status">{pick('Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '—').slice(0, 10)}</p>
    <p className="figure-caption mt-2">{pick('Top-level areas and subdivisions are counted separately; their totals are not added together.', '上级区域与下级行政单位分别统计，子级数据不叠加到上级总量。', 'Les zones de niveau supérieur et leurs subdivisions sont comptées séparément, sans additionner leurs totaux.')}</p>
  </div>;
}
