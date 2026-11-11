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
  if (!data) {
    if (mode === 'home-count') return error ? <span>—</span> : <GIDSLoader language={language} compact label={pick('Loading…', '读取中…', 'Chargement…')} />;
    if (error) return <p role="status">{pick('Current data is unavailable.', '当前数据暂时无法读取。', 'Les données actuelles sont indisponibles.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></p>;
    return <GIDSLoader language={language} compact={mode !== 'home-indicators'} className={mode === 'home-indicators' ? 'home-summary-loading' : ''} />;
  }
  const national: Summary[] = nationalRows(data), subdivisions = subdivisionRows(data), sourceCount = officialSourceCount(data);
  if (mode === 'home-count') return <span data-surveillance-version={version}>{number(metric === 'countries' ? national.length : metric === 'sources' ? sourceCount : data.total_diseases, language)}</span>;
  if (mode === 'country-links' || mode === 'recent-countries') {
    const rows = [...national].sort((a, b) => mode === 'recent-countries'
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
      [pick('National jurisdictions', '国家级辖区', 'Juridictions nationales'), national.length],
      [pick('Subnational datasets', '省级／地区数据集', 'Jeux infranationaux'), subdivisions.length],
      [pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), data.total_diseases],
      [pick('Official sources', '官方来源', 'Sources officielles'), sourceCount],
      ...(reportCount !== undefined ? [[pick('Published reports', '已发布报告', 'Rapports publiés'), reportCount]] : []),
    ].map(([name, value]) => <div key={String(name)}><dt className="academic-kicker mb-2">{name}</dt><dd className="font-semibold tabular-nums">{number(value, language)}</dd></div>)}</dl>
    <p className="figure-caption mt-3">{pick('Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '—').slice(0, 10)}</p>
    <p className="figure-caption mt-2">{pick('National and subnational coverage are counted separately.', '国家级与省级／地区覆盖分别统计，地区数据不叠加到全国总量。', 'Les couvertures nationale et infranationale sont comptées séparément, sans additionner leurs totaux.')}</p>
  </div>;
}
