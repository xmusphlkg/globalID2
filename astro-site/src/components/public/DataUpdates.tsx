import { useEffect, useMemo, useRef, useState } from 'react';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import './data-updates.css';

type Language = 'en' | 'zh' | 'fr';
type DataChannel = 'surveillance' | 'situation' | 'literature' | 'knowledge';
type Channel = DataChannel | 'website';
type DataEvent = { channel: DataChannel; published_at: string; scopes?: string[] };
type LoadStatus = 'loading' | 'ready' | 'error';
type SiteRelease = { version: string; date: string; title: string; summary: string };
type TimelineEvent = { kind: 'data'; event: DataEvent; date: string } | { kind: 'website'; release: SiteRelease; date: string };
const labels: Record<Channel, [string, string, string]> = {
  surveillance: ['Surveillance', '监测数据', 'Surveillance'],
  situation: ['Situation', '疫情通报', 'Situation'],
  literature: ['Research', '论文数据', 'Recherche'],
  knowledge: ['Knowledge', '疾病知识库', 'Connaissances'],
  website: ['Website', '网站版本', 'Site web'],
};
const paths: Record<DataChannel, string> = { surveillance: '/countries/', situation: '/situation/', literature: '/research/', knowledge: '/diseases/' };

export default function DataUpdates({ language, releases }: { language: Language; releases: SiteRelease[] }) {
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  const [months, setMonths] = useState<string[]>([]);
  const [month, setMonth] = useState('');
  const [filter, setFilter] = useState<'all' | Channel>('all');
  const [monthResult, setMonthResult] = useState<{ month: string; events: DataEvent[]; status: LoadStatus }>({ month: '', events: [], status: 'loading' });
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [indexStatus, setIndexStatus] = useState<LoadStatus>('loading');
  const [revision, setRevision] = useState(0);
  const latestMonth = useRef('');
  const { data: summary } = useSurveillanceSummary('/site-data/meta.json');
  const locale = language === 'zh' ? 'zh-CN' : language === 'fr' ? 'fr-FR' : 'en-US';
  const prefix = language === 'en' ? '' : `/${language}`;
  const releaseMonths = useMemo(() => Array.from(new Set(releases.map(item => item.date.slice(0, 7)))), [releases]);
  const allMonths = Array.from(new Set([...months, ...releaseMonths])).sort().reverse();
  useEffect(() => {
    let active = true;
    let timer: number;
    let controller: AbortController;
    const update = async () => {
      controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 15_000);
      try {
        const response = await fetch('/site-data/updates/latest.json', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('unavailable');
        const data = await response.json();
        if (data.schema_version !== 1 || !Array.isArray(data.months) || !data.months.every((v: unknown) => typeof v === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(v))) throw new Error('invalid');
        if (!active) return;
        const previousLatest = latestMonth.current;
        const nextLatest = Array.from(new Set<string>([...data.months, ...releaseMonths])).sort().reverse()[0] || '';
        latestMonth.current = nextLatest;
        setMonths(data.months);
        setMonth(current => !current || current === previousLatest || ![...data.months, ...releaseMonths].includes(current) ? nextLatest : current);
        const totals = data.counts;
        setCounts(totals && typeof totals === 'object' && (Object.keys(paths) as DataChannel[]).every(key => Number.isSafeInteger(totals[key]) && totals[key] >= 0) ? totals : null);
        setIndexStatus('ready'); setRevision(current => current + 1);
      } catch {
        if (active) { setIndexStatus('error'); setMonth(current => current || releaseMonths.slice().sort().reverse()[0] || ''); }
      } finally {
        window.clearTimeout(timeout);
        // Schedule only after completion so a slow response cannot overwrite a newer one.
        if (active) timer = window.setTimeout(update, 60_000);
      }
    };
    void update();
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [releaseMonths]);
  useEffect(() => {
    if (!month || !months.includes(month)) return;
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    const update = async () => {
      try {
        const response = await fetch(`/site-data/updates/${month}.json`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('unavailable');
        const data = await response.json();
        if (data.schema_version !== 1 || data.month !== month || !Array.isArray(data.events)) throw new Error('invalid');
        const valid = data.events.filter((event: DataEvent) => event && Object.hasOwn(paths, event.channel)
          && typeof event.published_at === 'string' && !Number.isNaN(Date.parse(event.published_at))
          && new Date(event.published_at).toISOString().slice(0, 7) === month
          && (event.scopes === undefined || (Array.isArray(event.scopes) && event.scopes.every(scope => typeof scope === 'string'))));
        if (active) setMonthResult({ month, events: valid, status: 'ready' });
      } catch {
        if (active) setMonthResult(current => ({ month, events: current.month === month ? current.events : [], status: 'error' }));
      } finally { window.clearTimeout(timeout); }
    };
    void update();
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [month, months, revision]);
  // Never show the previous month's events while the selected archive is loading.
  const events = months.includes(month) && monthResult.month === month ? monthResult.events : [];
  const error = indexStatus === 'error' || (monthResult.month === month && monthResult.status === 'error');
  const loading = indexStatus === 'loading' || (months.includes(month) && monthResult.month !== month);
  const timeline: TimelineEvent[] = [
    ...events.map((event): TimelineEvent => ({ kind: 'data', event, date: event.published_at })),
    ...releases.filter(release => release.date.slice(0, 7) === month).map((release): TimelineEvent => ({ kind: 'website', release, date: release.date })),
  ];
  const selected = timeline.filter(item => filter === 'all' || (item.kind === 'website' ? filter === 'website' : item.event.channel === filter))
    .sort((a, b) => Date.parse(b.date) - Date.parse(a.date));
  const availableCountries = Array.isArray(summary?.countries) ? summary.countries.filter((item: Record<string, unknown>) => item?.data_available === true) : null;
  const curvePoints = availableCountries && availableCountries.length > 0 && availableCountries.every((item: Record<string, unknown>) => Number.isSafeInteger(item.record_count) && Number(item.record_count) >= 0)
    ? availableCountries.reduce((sum: number, item: Record<string, unknown>) => sum + Number(item.record_count), 0) : null;
  const total = counts ? (Object.keys(paths) as DataChannel[]).reduce((sum, key) => sum + counts[key], 0) : null;
  const displayNumber = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? new Intl.NumberFormat(locale).format(value) : '—';
  return <>
    <div className="updates-metrics site-stat-grid" aria-label={pick('Publication overview', '发布概览', 'Aperçu des publications')}>
      <div className="site-stat-item"><strong>{displayNumber(summary?.total_countries)}</strong><span>{pick('Reporting jurisdictions', '有数据的国家及地区', 'Juridictions avec données')}</span></div>
      <div className="site-stat-item"><strong>{displayNumber(curvePoints)}</strong><span>{pick('Published curve points', '已发布曲线数据点', 'Points de courbe publiés')}</span></div>
      <div className="site-stat-item"><strong>{displayNumber(summary?.total_diseases)}</strong><span>{pick('Diseases in catalogue', '疾病目录收录', 'Maladies au catalogue')}</span></div>
      <div className="site-stat-item"><strong>{displayNumber(total)}</strong><span>{pick('Recorded data release batches', '已记录的数据发布批次', 'Lots de données publiés enregistrés')}</span></div>
      <div className="site-stat-item"><strong>{displayNumber(releases.length)}</strong><span>{pick('Documented website versions', '已记录的网站版本', 'Versions du site documentées')}</span></div>
    </div>
    <p className="updates-metrics-note">{pick('Coverage and curve points come from the current surveillance summary. Geographic levels may overlap. Batches count retained publication records, not data points.', '覆盖规模和曲线数据点来自当前监测摘要，国家与地区层级可能重叠；发布批次统计现存记录，不等同于数据条数。', 'La couverture et les points de courbe proviennent du résumé de surveillance ; les niveaux géographiques peuvent se recouper. Les lots comptent les publications conservées, pas les observations.')}</p>
    <div className="updates-panel card">
      <div className="updates-heading"><div><span className="academic-kicker">{pick('PUBLICATION HISTORY', '发布历史', 'HISTORIQUE DES PUBLICATIONS')}</span><h2>{pick('Latest activity', '最新动态', 'Activité récente')}</h2></div><span className="updates-refresh">● {pick('Checks every minute', '每分钟检查更新', 'Vérification chaque minute')}</span></div>
      <div className="updates-controls">
        <label>{pick('Month (UTC)', '月份（UTC）', 'Mois (UTC)')} <select className="site-control-input focus-ring" value={month} onChange={event => setMonth(event.target.value)} disabled={!allMonths.length}>{allMonths.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>{pick('Update type', '更新类型', 'Type de mise à jour')} <select className="site-control-input focus-ring" value={filter} onChange={event => setFilter(event.target.value as 'all' | Channel)}><option value="all">{pick('All updates', '全部更新', 'Toutes les mises à jour')}</option>{(Object.keys(labels) as Channel[]).map(key => <option key={key} value={key}>{labels[key][language === 'zh' ? 1 : language === 'fr' ? 2 : 0]}</option>)}</select></label>
      </div>
      <div role="status" className="updates-status">{error ? pick('Data update history is temporarily unavailable. Website versions remain available; checking will resume automatically.', '数据更新记录暂不可用，网站版本仍可查看；系统将自动重试。', 'Historique des données temporairement indisponible. Les versions du site restent accessibles ; une nouvelle vérification sera effectuée.') : loading ? pick('Loading data updates…', '正在加载数据更新记录…', 'Chargement des mises à jour…') : !selected.length ? pick('No published updates for this selection.', '所选范围暂无发布记录。', 'Aucune mise à jour publiée pour cette sélection.') : ''}</div>
      <ol className="updates-list">{selected.map((item, index) => item.kind === 'website' ? <li key={`website-${item.release.version}`}>
        <span className="updates-marker updates-marker-website" aria-hidden="true" />
        <div><span className="updates-kind">{labels.website[language === 'zh' ? 1 : language === 'fr' ? 2 : 0]} · v{item.release.version}</span><h3>{item.release.title}</h3><p>{item.release.summary}</p><a href={`${prefix}/changelog/#v${encodeURIComponent(item.release.version)}`}>{pick('View release notes', '查看版本与版式更新', 'Voir les notes de version')} →</a></div>
        <time dateTime={item.release.date}>{new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${item.release.date}T00:00:00Z`))}</time>
      </li> : <li key={`${item.event.channel}-${item.event.published_at}-${index}`}>
        <span className={`updates-marker updates-marker-${item.event.channel}`} aria-hidden="true" />
        <div><span className="updates-kind">{labels[item.event.channel][language === 'zh' ? 1 : language === 'fr' ? 2 : 0]}</span><h3>{pick('Data published', '数据已发布', 'Données publiées')}</h3>
          {Array.isArray(item.event.scopes) && item.event.scopes.length > 0 && <p>{pick('Published scopes', '发布范围', 'Périmètres publiés')}：{item.event.scopes.join(' · ')}</p>}
          <a href={`${prefix}${paths[item.event.channel]}`}>{pick('Explore data', '查看相关数据', 'Explorer les données')} →</a></div>
        <time dateTime={item.event.published_at}>{new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.event.published_at))}</time>
      </li>)}</ol>
    </div>
  </>;
}
