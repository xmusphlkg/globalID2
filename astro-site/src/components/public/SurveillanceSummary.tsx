import { useEffect, useState } from 'react';
import { surveillanceData } from '../../lib/surveillance-data.ts';

type Language = 'en' | 'zh' | 'fr';
type Summary = Record<string, any>;
type Mode = 'country-coverage' | 'country-indicators' | 'country-diseases' | 'country-disease-indicators' | 'country-context' | 'disease-coverage' | 'disease-countries' | 'downloads' | 'sources' | 'home-indicators' | 'country-card' | 'coverage-caption' | 'global-coverage' | 'country-links' | 'recent-countries';
const number = (value: unknown, language: Language) => typeof value === 'number' && Number.isFinite(value)
  ? value.toLocaleString(language === 'zh' ? 'zh-CN' : language === 'fr' ? 'fr-FR' : 'en-US') : '—';
const label = (value: Summary, language: Language) => String(value[`name_${language}`] || value.name_en || value.name || value.code || value.disease_id || '');
const coverage = (value: Summary) => [value.date_range?.start, value.date_range?.end].filter(Boolean).join(' → ') || '—';
const safeUrl = (raw: unknown) => {
  if (typeof raw !== 'string') return null;
  try { const value = new URL(raw, 'https://local.invalid'); return ['http:', 'https:'].includes(value.protocol) ? raw : null; } catch { return null; }
};

export function useSurveillanceSummary(dataUrl: string) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ data?: Summary; version?: string; error?: boolean }>({});
  useEffect(() => {
    let cancelled = false;
    setState({});
    Promise.all([surveillanceData.read<Summary>(dataUrl), surveillanceData.manifest()]).then(([data, manifest]) => {
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid surveillance summary');
      if (!cancelled) setState({ data, version: manifest?.release_id });
    }).catch(() => { if (!cancelled) setState({ error: true }); });
    return () => { cancelled = true; };
  }, [dataUrl, attempt]);
  return { ...state, retry: () => setAttempt(value => value + 1) };
}

export default function SurveillanceSummary({ dataUrl, mode, language = 'en', code, diseaseId }: { dataUrl: string; mode: Mode; language?: Language; code?: string; diseaseId?: string }) {
  const { data: payload, version, error, retry } = useSurveillanceSummary(dataUrl);
  const data = payload && mode === 'country-card' ? (payload.countries ?? []).find((row: Summary) => String(row.code).toUpperCase() === code?.toUpperCase()) : payload;
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  const prefix = language === 'en' ? '' : `/${language}`;
  if (!data) return <div className="text-sm text-[rgb(var(--text-muted))]" role="status">
    {error ? <>{pick('Current data is unavailable.', '当前数据暂不可用。', 'Les données actuelles sont indisponibles.')} {mode !== 'country-card' && <button type="button" className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button>}</>
      : pick('Loading current data…', '正在加载最新数据…', 'Chargement des données actuelles…')}
  </div>;
  const timestamp = data.generated_at;
  const revision = <p className="mt-3 text-xs text-[rgb(var(--text-muted))]" data-surveillance-version={version}>
    {timestamp ? `${pick('Data updated', '数据更新于', 'Données mises à jour')} ${String(timestamp).slice(0, 10)}` : ''}
  </p>;
  if (mode === 'country-card') return <div data-surveillance-version={version}><div className="grid grid-cols-3 gap-2 border-t border-[rgb(var(--border))] pt-3 text-center text-sm">
    {[[pick('Cases', '病例', 'Cas'), data.total_cases], [pick('Deaths', '死亡', 'Décès'), data.total_deaths], [pick('Diseases', '疾病', 'Maladies'), data.disease_count]].map(([name, value]) => <div key={String(name)}><strong className="block tabular-nums">{number(value, language)}</strong><span className="text-xs text-[rgb(var(--text-muted))]">{name}</span></div>)}
  </div><p className="mt-2 text-xs text-[rgb(var(--text-muted))]">{coverage(data)}</p></div>;
  const national = (data.countries ?? []).filter((row: Summary) => row.location_type !== 'subdivision' && !row.parent_code && row.data_available !== false);
  if (mode === 'coverage-caption') return <p className="figure-caption">{number(national.length, language)} {pick('countries with data', '个国家有监测数据', 'pays avec des données')} · {pick('Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '—').slice(0, 10)}</p>;
  if (mode === 'global-coverage') {
    const starts = national.map((row: Summary) => row.date_range?.start).filter(Boolean).sort();
    const ends = national.map((row: Summary) => row.date_range?.end).filter(Boolean).sort();
    return <p className="figure-caption" data-surveillance-version={version}>{pick('Coverage across national series', '国家级序列时间覆盖', 'Couverture des séries nationales')} {starts[0] ?? '—'} → {ends.at(-1) ?? '—'}</p>;
  }
  if (mode === 'country-links' || mode === 'recent-countries') {
    const rows = [...national].sort((a, b) => mode === 'recent-countries'
      ? String(b.date_range?.end ?? '').localeCompare(String(a.date_range?.end ?? ''))
      : Number(b.record_count ?? 0) - Number(a.record_count ?? 0)).slice(0, mode === 'recent-countries' ? 3 : 6);
    return <div className={mode === 'recent-countries' ? 'grid gap-3' : 'grid gap-3 sm:grid-cols-2'} data-surveillance-version={version}>{rows.map(row => <a key={row.code} className="site-action-secondary block border p-4 text-sm" href={`${prefix}/countries/${String(row.code).toLowerCase()}/`}><strong>{label(row, language)}</strong><span className="block mt-1 text-xs text-[rgb(var(--text-muted))]">{coverage(row)} · {number(row.disease_count, language)} {pick('diseases', '种疾病', 'maladies')}</span></a>)}</div>;
  }
  if (mode === 'country-coverage' || mode === 'disease-coverage') return <div className="academic-meta-grid xl:grid-cols-2">
    <div className="academic-meta-item"><div className="academic-meta-label">{pick('Coverage window', '覆盖区间', 'Période couverte')}</div><div className="academic-meta-value text-sm">{coverage(data)}</div></div>
    <div className="academic-meta-item"><div className="academic-meta-label">{mode === 'country-coverage' ? pick('Tracked diseases', '追踪疾病数', 'Maladies suivies') : pick('Reporting countries', '报告国家数', 'Pays déclarants')}</div><div className="academic-meta-value font-serif">{number(mode === 'country-coverage' ? data.disease_count : data.country_count, language)}</div></div>
    {revision}
  </div>;
  if (mode === 'country-indicators' || mode === 'home-indicators') {
    const values = mode === 'country-indicators' ? [
      [pick('Total cases', '累计病例', 'Cas cumulés'), data.total_cases],
      [pick('Total deaths', '累计死亡', 'Décès cumulés'), data.total_deaths],
      [pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), data.disease_count],
    ] : [
      [pick('Countries and regions', '国家与地区', 'Pays et régions'), data.total_countries ?? data.country_count ?? data.countries?.length],
      [pick('Diseases tracked', '追踪疾病数', 'Maladies suivies'), data.total_diseases ?? data.disease_count ?? data.diseases?.length],
      [pick('National datasets', '国家级数据集', 'Jeux nationaux'), national.length],
    ];
    return <div><div className="site-stat-grid grid grid-cols-2 sm:grid-cols-3 gap-px border">
      {values.map(([name, value]) => <div className="site-stat-item px-6 py-6 text-center" key={String(name)}><div className="academic-kicker mb-2">{name}</div><div className="font-serif text-3xl font-semibold tabular-nums text-teal-500">{number(value, language)}</div></div>)}
    </div>{revision}</div>;
  }
  if (mode === 'country-diseases') return <div><ul className="mt-4 flex flex-wrap gap-2">
    {[...(data.diseases ?? [])].sort((a, b) => Number(b.total_cases ?? 0) - Number(a.total_cases ?? 0)).slice(0, 16).map((row: Summary) => <li key={String(row.disease_id ?? row.id)}><a href={`${prefix}/diseases/${encodeURIComponent(row.slug || row.disease_id || row.id)}/`} className="site-action-secondary inline-flex px-3 py-1.5 text-sm border">{label(row, language)}</a></li>)}
  </ul>{!(data.diseases?.length > 0) && <p className="figure-caption">{pick('No mapped disease series are available.', '暂无已映射的疾病序列。', 'Aucune série de maladie associée.')}</p>}</div>;
  if (mode === 'country-context') return <div className="space-y-4">
    {data[`brief_${language}`] && <p className="text-sm leading-6 text-[rgb(var(--text-muted))]">{data[`brief_${language}`]}</p>}
    <div className="grid gap-4 sm:grid-cols-2">{[
      ['surveillance_system', pick('Surveillance system', '监测系统', 'Système de surveillance')],
      ['interpretation', pick('Current interpretation', '当前解读', 'Interprétation actuelle')],
      ['reporting_cadence', pick('Reporting cadence', '报告频率', 'Fréquence de déclaration')],
      ['limitations', pick('Data limitations', '数据限制', 'Limites des données')],
    ].map(([key, name]) => data[`${key}_${language}`] || data[`${key}_en`] ? <div className="border border-[rgb(var(--border))] p-4" key={key}><div className="academic-meta-label">{name}</div><p className="mt-2 text-sm leading-6 text-[rgb(var(--text-muted))]">{data[`${key}_${language}`] || data[`${key}_en`]}</p></div> : null)}</div>{revision}
  </div>;
  if (mode === 'country-disease-indicators') {
    const series = (data.diseases ?? []).find((entry: Summary) => String(entry.disease_id ?? entry.id).toUpperCase() === diseaseId?.toUpperCase());
    const start = series?.latest_period_start;
    const end = series?.latest_period_end_exclusive ? new Date(`${series.latest_period_end_exclusive}T00:00:00Z`) : null;
    if (end && Number.isFinite(end.getTime())) end.setUTCDate(end.getUTCDate() - 1);
    const lastPeriod = start ? end && Number.isFinite(end.getTime()) && end.toISOString().slice(0, 10) !== start ? `${start} → ${end.toISOString().slice(0, 10)}` : start : '—';
    return <div className="academic-meta-grid mt-6" data-surveillance-version={version}>{[
      [pick('Coverage', '覆盖区间', 'Période couverte'), series ? coverage(series) : '—'],
      [pick('Reporting granularity', '报告粒度', 'Granularité des rapports'), series?.period_granularity ?? '—'],
      [pick('Latest reported value', '最新报告值', 'Dernière valeur publiée'), number(series?.latest_cases, language)],
      [pick('Latest reporting period', '最新报告期', 'Dernière période déclarée'), lastPeriod],
    ].map(([name, value]) => <div className="academic-meta-item" key={name}><div className="academic-meta-label">{name}</div><div className="academic-meta-value text-sm">{value}</div></div>)}</div>;
  }
  if (mode === 'disease-countries') return <div><p className="figure-caption">{number(data.country_count, language)} {pick('reporting countries', '个报告国家', 'pays déclarants')}</p><ul className="grid gap-2 mt-4 sm:grid-cols-3 lg:grid-cols-5">
    {(data.reporting_countries ?? []).map((row: Summary) => <li key={row.code}><a href={`${prefix}/countries/${encodeURIComponent(String(row.code).toLowerCase())}/`} className="site-action-secondary flex items-center gap-2 px-3 py-2 text-sm border"><span>{label(row, language)}</span><span className="text-xs text-[rgb(var(--text-muted))]">{row.code}</span></a></li>)}
  </ul></div>;
  const download = data.download;
  const sourceGroups = Array.isArray(data.source_info) ? data.source_info : [data.source_info ?? {}];
  const sources = sourceGroups.flatMap((group: Summary) => group.sources ?? (group.primary_url ? [{ url: group.primary_url, label: group.primary_label }] : []));
  const sourceLinks = sources.length > 0 ? <ul className="flex flex-wrap gap-3 text-sm">{sources.map((source: Summary, index: number) => { const url = safeUrl(source.url); return url ? <li key={`${url}:${index}`}><a className="underline" href={url} target="_blank" rel="noopener noreferrer">{source.label || source.country_name || url}</a></li> : null; })}</ul> : <p className="figure-caption">{pick('Source details are recorded with the published series.', '来源详情随已发布序列提供。', 'Les sources accompagnent les séries publiées.')}</p>;
  if (mode === 'sources') return <div data-surveillance-version={version}>{sourceLinks}</div>;
  return <div className="space-y-4" data-surveillance-version={version}>
    {sourceLinks}
    {!download && <p className="figure-caption">{pick('Download archives are updated separately from live curves.', '下载档案与实时曲线独立更新。', 'Les archives sont mises à jour séparément des courbes.')} <a className="underline" href={`${prefix}/downloads/`}>{pick('Browse downloads', '浏览下载档案', 'Parcourir les téléchargements')}</a></p>}
    {download?.generated_at && <p className="figure-caption">{pick('Download snapshot', '下载文件版本', 'Version des téléchargements')} {String(download.generated_at).slice(0, 10)}</p>}
    {(download?.parts ?? []).map((part: Summary) => <div className="flex flex-wrap gap-3 items-center border-b border-[rgb(var(--border))] py-3" key={part.id}>
      <span className="font-medium text-sm">{part.label || part.id}</span><span className="text-xs text-[rgb(var(--text-muted))]">{[part.date_range?.start, part.date_range?.end].filter(Boolean).join(' → ')}</span>
      {Object.entries(part.files ?? {}).map(([format, value]: [string, any]) => { const url = safeUrl(value?.url); return url ? <a key={format} href={url} className="site-action-secondary px-3 py-1 text-xs border" target="_blank" rel="noopener noreferrer">{format.toUpperCase()}</a> : null; })}
    </div>)}
    {revision}
  </div>;
}
