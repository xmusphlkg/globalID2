import GIDSLoader from '../GIDSLoader';
import { useEffect, useMemo, useRef, useState } from 'react';
import { dailyCounts, daysCovering, monthEnd, monthsCovering, rangeProblem } from '../../lib/data-updates';
import { useSurveillanceSummary } from './useSurveillanceSummary';
import './data-updates.css';

type Language = 'en' | 'zh' | 'fr';
type DataChannel = 'surveillance' | 'situation' | 'literature' | 'knowledge';
type Channel = DataChannel | 'website';
type DataEvent = { channel: DataChannel; published_at: string; scopes?: string[] };
type LoadStatus = 'loading' | 'ready' | 'error';
type SiteRelease = { version: string; date: string; title: string; summary: string };
type TimelineEvent = { kind: 'data'; event: DataEvent; date: string } | { kind: 'website'; release: SiteRelease; date: string };
type Archive = { events: DataEvent[]; status: Exclude<LoadStatus, 'loading'> };
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
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [filter, setFilter] = useState<'all' | Channel>('all');
  const [archives, setArchives] = useState<Record<string, Archive>>({});
  const [counts, setCounts] = useState<Record<string, number> | null>(null);
  const [indexStatus, setIndexStatus] = useState<LoadStatus>('loading');
  const [revision, setRevision] = useState(0);
  const followLatest = useRef(true);
  const { data: summary } = useSurveillanceSummary('/site-data/meta.json');
  const locale = language === 'zh' ? 'zh-CN' : language === 'fr' ? 'fr-FR' : 'en-US';
  const prefix = language === 'en' ? '' : `/${language}`;
  const releaseMonths = useMemo(() => Array.from(new Set(releases.map(item => item.date.slice(0, 7)))), [releases]);
  const allMonths = Array.from(new Set([...months, ...releaseMonths])).sort().reverse();
  const problem = rangeProblem(from, to);
  const neededMonths = problem || !from || !to ? [] : monthsCovering(from, to).filter(month => months.includes(month));
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
        const nextLatest = Array.from(new Set<string>([...data.months, ...releaseMonths])).sort().reverse()[0] || '';
        setMonths(data.months);
        if (followLatest.current && nextLatest) {
          setFrom(`${nextLatest}-01`);
          setTo(monthEnd(nextLatest));
        }
        const totals = data.counts;
        setCounts(totals && typeof totals === 'object' && (Object.keys(paths) as DataChannel[]).every(key => Number.isSafeInteger(totals[key]) && totals[key] >= 0) ? totals : null);
        setIndexStatus('ready'); setRevision(current => current + 1);
      } catch {
        if (active) {
          setIndexStatus('error');
          if (followLatest.current) {
            const fallback = releaseMonths.slice().sort().reverse()[0] || '';
            if (fallback) {
              setFrom(current => current || `${fallback}-01`);
              setTo(current => current || monthEnd(fallback));
            }
          }
        }
      } finally {
        window.clearTimeout(timeout);
        if (active) timer = window.setTimeout(update, 60_000);
      }
    };
    void update();
    return () => { active = false; window.clearTimeout(timer); controller.abort(); };
  }, [releaseMonths]);
  useEffect(() => {
    if (!neededMonths.length) return;
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    const update = async () => {
      const updates: Record<string, Archive | 'error'> = {};
      await Promise.all(neededMonths.map(async month => {
        try {
          const response = await fetch(`/site-data/updates/${month}.json`, { cache: 'no-store', signal: controller.signal });
          if (!response.ok) throw new Error('unavailable');
          const data = await response.json();
          if (data.schema_version !== 1 || data.month !== month || !Array.isArray(data.events)) throw new Error('invalid');
          const valid = data.events.filter((event: DataEvent) => event && Object.hasOwn(paths, event.channel)
            && typeof event.published_at === 'string' && !Number.isNaN(Date.parse(event.published_at))
            && new Date(event.published_at).toISOString().slice(0, 7) === month
            && (event.scopes === undefined || (Array.isArray(event.scopes) && event.scopes.every(scope => typeof scope === 'string'))));
          updates[month] = { events: valid, status: 'ready' };
        } catch {
          updates[month] = 'error';
        }
      }));
      if (!active) return;
      setArchives(current => {
        const next = { ...current };
        for (const [month, value] of Object.entries(updates)) {
          if (value === 'error') next[month] = { events: current[month]?.events ?? [], status: 'error' };
          else next[month] = value;
        }
        return next;
      });
    };
    void update();
    return () => { active = false; window.clearTimeout(timeout); controller.abort(); };
  }, [neededMonths.join('|'), revision]);
  const waiting = neededMonths.some(month => !archives[month]);
  const events = waiting || problem ? [] : neededMonths.flatMap(month => archives[month]?.events ?? []);
  const error = indexStatus === 'error' || neededMonths.some(month => archives[month]?.status === 'error');
  const loading = indexStatus === 'loading' || waiting;
  const timeline: TimelineEvent[] = problem ? [] : [
    ...events.filter(event => event.published_at.slice(0, 10) >= from && event.published_at.slice(0, 10) <= to).map((event): TimelineEvent => ({ kind: 'data', event, date: event.published_at })),
    ...releases.filter(release => release.date >= from && release.date <= to).map((release): TimelineEvent => ({ kind: 'website', release, date: release.date })),
  ];
  const selected = timeline.filter(item => filter === 'all' || (item.kind === 'website' ? filter === 'website' : item.event.channel === filter))
    .sort((a, b) => Date.parse(b.date) - Date.parse(a.date));
  const days = problem ? [] : daysCovering(from, to);
  const trend = dailyCounts(days, selected.map(item => item.date));
  const max = Math.max(1, ...trend.map(row => row.count));
  const rangeLabel = from && to ? `${from}–${to}` : '—';
  const availableCountries = Array.isArray(summary?.countries) ? summary.countries.filter((item: Record<string, unknown>) => item?.data_available === true) : null;
  const curvePoints = availableCountries && availableCountries.length > 0 && availableCountries.every((item: Record<string, unknown>) => Number.isSafeInteger(item.record_count) && Number(item.record_count) >= 0)
    ? availableCountries.reduce((sum: number, item: Record<string, unknown>) => sum + Number(item.record_count), 0) : null;
  const total = counts ? (Object.keys(paths) as DataChannel[]).reduce((sum, key) => sum + counts[key], 0) : null;
  const displayNumber = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? new Intl.NumberFormat(locale).format(value) : '—';
  const channelName = (key: Channel) => labels[key][language === 'zh' ? 1 : language === 'fr' ? 2 : 0];
  const statusText = problem === 'reversed'
    ? pick('The start date must not be later than the end date.', '起始日期不能晚于截止日期。', 'La date de début ne doit pas dépasser la date de fin.')
    : problem === 'wide'
      ? pick('Choose a range of 24 months or less.', '请选择不超过 24 个月的范围。', 'Choisissez une période de 24 mois au plus.')
      : error
        ? pick('Data update history is temporarily unavailable. Website versions remain available; checking will resume automatically.', '数据更新记录暂不可用，网站版本仍可查看；系统将自动重试。', 'Historique des données temporairement indisponible. Les versions du site restent accessibles ; une nouvelle vérification sera effectuée.')
        : '';
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
        <label>{pick('From (UTC)', '起始日期（UTC）', 'Depuis (UTC)')} <input className="site-control-input focus-ring" type="date" value={from} onChange={event => { followLatest.current = false; setFrom(event.target.value); }} /></label>
        <label>{pick('Through (UTC)', '截止日期（UTC）', 'Jusqu’au (UTC)')} <input className="site-control-input focus-ring" type="date" value={to} onChange={event => { followLatest.current = false; setTo(event.target.value); }} /></label>
        <label>{pick('Update type', '更新类型', 'Type de mise à jour')} <select className="site-control-input focus-ring" value={filter} onChange={event => setFilter(event.target.value as 'all' | Channel)}><option value="all">{pick('All updates', '全部更新', 'Toutes les mises à jour')}</option>{(Object.keys(labels) as Channel[]).map(key => <option key={key} value={key}>{channelName(key)}</option>)}</select></label>
      </div>
      <section className="updates-trend" aria-labelledby="updates-trend-title">
        <div className="updates-trend-heading"><div><p className="academic-kicker">{pick('DAILY ACTIVITY', '每日动态', 'ACTIVITÉ QUOTIDIENNE')}</p><h3 id="updates-trend-title">{pick('Publication records by day', '每日发布记录', 'Publications par jour')}</h3></div><span>{rangeLabel}</span></div>
        <p className="updates-trend-note">{pick('Each bar counts retained publication records on that UTC day, including website versions when they are included in the filter. A day with no bar height had no retained record. Counts are not observations or cases.', '每根柱表示该 UTC 日保留的发布记录数，筛选包含网站版本时一并计入。没有高度的日期表示当天没有保留记录。这不是观测值或病例数。', 'Chaque barre compte les publications conservées ce jour UTC, versions du site comprises lorsque le filtre les inclut. Une barre absente signifie qu’aucun enregistrement n’est conservé. Ce ne sont pas des observations ou des cas.')}</p>
        {problem || loading || !trend.length ? null : <><div className="updates-trend-chart" role="img" aria-label={pick(`Daily publication records, ${rangeLabel}`, `${rangeLabel} 每日发布记录`, `Publications quotidiennes, ${rangeLabel}`)}><svg viewBox="0 0 960 190" width="960" height="190" aria-hidden="true">
          <line x1="12" x2="948" y1="154" y2="154" stroke="currentColor" opacity=".2" />
          {trend.map((row, index) => { const step = 936 / trend.length; const height = row.count / max * 132; return <g key={row.day}><title>{`${row.day}: ${row.count}`}</title><rect x={12 + step * index + step * .15} y={154 - height} width={Math.max(1, step * .7)} height={Math.max(row.count ? 1 : 0, height)} rx="1" fill="currentColor" />{(index % Math.max(1, Math.ceil(trend.length / 8)) === 0 || index === trend.length - 1) && <text x={12 + step * index + step / 2} y="177" textAnchor="middle">{row.day.slice(5)}</text>}</g>; })}
        </svg></div><details className="updates-trend-table"><summary>{pick('View daily counts', '查看每日数量', 'Voir les volumes quotidiens')}</summary><div><table><caption className="sr-only">{pick('Daily publication records', '每日发布记录', 'Publications quotidiennes')}</caption><thead><tr><th scope="col">{pick('Day (UTC)', '日期（UTC）', 'Jour (UTC)')}</th><th scope="col">{pick('Records', '记录数', 'Enregistrements')}</th></tr></thead><tbody>{trend.map(row => <tr key={row.day}><th scope="row">{row.day}</th><td>{row.count.toLocaleString(locale)}</td></tr>)}</tbody></table></div></details></>}
      </section>
      <div className="updates-status" role={statusText || !loading ? 'status' : undefined} aria-live="polite">{statusText || (loading ? <GIDSLoader language={language} compact label={pick('Loading data updates…', '正在读取更新记录…', 'Chargement des mises à jour…')} /> : !selected.length ? pick('No published updates for this selection.', '所选范围暂无发布记录。', 'Aucune mise à jour publiée pour cette sélection.') : '')}</div>
      <ol className="updates-list">{selected.map((item, index) => item.kind === 'website' ? <li key={`website-${item.release.version}`}>
        <span className="updates-marker updates-marker-website" aria-hidden="true" />
        <div><span className="updates-kind">{channelName('website')} · v{item.release.version}</span><h3>{item.release.title}</h3><p>{item.release.summary}</p><a href={`${prefix}/changelog/#v${encodeURIComponent(item.release.version)}`}>{pick('View release notes', '查看版本与版式更新', 'Voir les notes de version')} →</a></div>
        <time dateTime={item.release.date}>{new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${item.release.date}T00:00:00Z`))}</time>
      </li> : <li key={`${item.event.channel}-${item.event.published_at}-${index}`}>
        <span className={`updates-marker updates-marker-${item.event.channel}`} aria-hidden="true" />
        <div><span className="updates-kind">{channelName(item.event.channel)}</span><h3>{pick('Data published', '数据已发布', 'Données publiées')}</h3>
          {Array.isArray(item.event.scopes) && item.event.scopes.length > 0 && <p>{pick('Published scopes', '发布范围', 'Périmètres publiés')}：{item.event.scopes.join(' · ')}</p>}
          <a href={`${prefix}${paths[item.event.channel]}`}>{pick('Explore data', '查看相关数据', 'Explorer les données')} →</a></div>
        <time dateTime={item.event.published_at}>{new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.event.published_at))}</time>
      </li>)}</ol>
    </div>
  </>;
}
