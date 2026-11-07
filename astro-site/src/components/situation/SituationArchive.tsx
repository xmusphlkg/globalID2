import { useEffect, useState } from 'react';
import { situationData } from '../../lib/situation-data';
import { weeklyDiff } from './text';
import { alertSummary, countsLine, countryName, diseaseName, formatDay, sourceLabel } from './noticeLabels';
import { useSituationData } from './useSituationData';
import type { Locale, OutbreakFile, WeeklyChange } from '../../utils/situationTypes';

const monthOfWeek = (period: string) => {
  const [year, week] = period.split('-W').map(Number);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  jan4.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7);
  return jan4.toISOString().slice(0, 7);
};
const weekOfDay = (day: string) => {
  const when = new Date(`${String(day).slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(when.getTime())) return '';
  when.setUTCDate(when.getUTCDate() - ((when.getUTCDay() + 6) % 7) + 3);
  const year = when.getUTCFullYear(), jan4 = new Date(Date.UTC(year, 0, 4));
  const week = 1 + Math.round((((when.getTime() - jan4.getTime()) / 86_400_000) - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
};

function ArchiveOutbreak({ id, period, locale }: { id: string; period: string; locale: Locale }) {
  const [file, setFile] = useState<OutbreakFile>();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const pick = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  const load = () => {
    if (file || loading) return;
    setLoading(true); setError(false);
    situationData.read<OutbreakFile>(`/situation/outbreaks/${encodeURIComponent(id)}.json`)
      .then(value => { if (value.schema_version !== 'gids.situation_alerts.v1' || !Array.isArray(value.alerts)) throw new Error('Invalid outbreak'); setFile(value); })
      .catch(() => setError(true)).finally(() => setLoading(false));
  };
  return <details className="border p-4" onToggle={event => { if (event.currentTarget.open) load(); }}>
    <summary className="cursor-pointer font-semibold">{file ? diseaseName(file, locale) : pick('Outbreak details', '疫情详情', 'Détails de la flambée')} · {id}</summary>
    {error && <p role="status">{pick('Details are unavailable.', '详情暂不可用。', 'Détails indisponibles.')} <button className="underline" onClick={load}>{pick('Retry', '重试', 'Réessayer')}</button></p>}
    {loading && <p role="status">{pick('Loading…', '正在加载…', 'Chargement…')}</p>}
    <ul className="mt-3 space-y-3">{(file?.alerts ?? []).filter(alert => weekOfDay(alert.published_date) === period).map(alert => <li className="border-t pt-3" key={alert.alert_id}>
      <strong>{countryName(alert, locale)}</strong> · {formatDay(alert.published_date, locale)}
      {countsLine(alert, locale) && <p>{countsLine(alert, locale)}</p>}
      {alertSummary(alert, locale) && <p className="text-sm">{alertSummary(alert, locale)}</p>}
      {alert.quote && <blockquote className="text-sm italic">{alert.quote}</blockquote>}
      <a className="underline text-sm" href={alert.url} target="_blank" rel="noopener noreferrer">{sourceLabel(alert, locale)}</a>
    </li>)}</ul>
  </details>;
}

export default function SituationArchive({ locale, mode, period: fixedPeriod, month: fixedMonth }: { locale: Locale; mode: 'weekly' | 'monthly'; period?: string; month?: string }) {
  const { data, version, error, retry } = useSituationData<{ periods: WeeklyChange[] }>('/situation/weekly/index.json');
  const [query, setQuery] = useState({ period: '', month: '' });
  useEffect(() => { const params = new URLSearchParams(location.search); setQuery({ period: params.get('period') || '', month: params.get('month') || '' }); }, []);
  const pick = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  const prefix = locale === 'en' ? '' : `/${locale}`;
  if (!data) return <p role="status">{error ? <>{pick('Situation archive is unavailable.', '态势归档暂不可用。', 'Archive indisponible.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></> : pick('Loading archive…', '正在加载归档…', 'Chargement des archives…')}</p>;
  const weeks = [...(data.periods ?? [])].filter(row => /^\d{4}-W\d{2}$/.test(row.period_key)).sort((a, b) => b.period_key.localeCompare(a.period_key));
  const month = fixedMonth || query.month, period = fixedPeriod || query.period;
  const selected = weeks.find(row => row.period_key === period);
  const shown = month ? weeks.filter(row => monthOfWeek(row.period_key) === month) : weeks;
  const months = [...new Set(weeks.map(row => monthOfWeek(row.period_key)))].sort().reverse();
  return <div className="space-y-5" data-situation-version={version}>
    {!weeks.length && <p>{pick('No weekly change has been published yet.', '尚未发布每周变化。', 'Aucun changement hebdomadaire publié.')}</p>}
    {mode === 'monthly' && !month ? <ul className="space-y-3">{months.map(value => <li key={value}><a className="site-action-secondary border p-3 block" href={`${prefix}/situation/monthly/?month=${value}`}>{value} · {weeks.filter(row => monthOfWeek(row.period_key) === value).length} {pick('weeks', '周', 'semaines')}</a></li>)}</ul> : !selected && <ul className="space-y-3">{shown.map(row => <li key={row.period_key}><a className="site-action-secondary border p-3 block" href={`${prefix}/situation/weekly/?period=${row.period_key}`}><strong>{row.period_key}</strong><span className="block text-sm mt-1">{weeklyDiff(row, locale)}</span></a></li>)}</ul>}
    {selected && <section className="space-y-4"><h2 className="text-xl font-semibold">{selected.period_key}</h2><p>{weeklyDiff(selected, locale)}</p>{[...new Set([...selected.new_outbreaks, ...selected.updated_outbreaks])].map(id => <ArchiveOutbreak key={`${period}:${id}`} id={id} period={selected.period_key} locale={locale} />)}</section>}
    {period && !selected && <p>{pick('This week is absent from the current archive.', '当前归档不包含该周。', 'Cette semaine ne figure pas dans les archives actuelles.')}</p>}
  </div>;
}
