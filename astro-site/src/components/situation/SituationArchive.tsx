import GIDSLoader from '../GIDSLoader';
import { useState } from 'react';
import { situationData } from '../../lib/situation-data';
import { weeklyDiff } from './text';
import { alertSummary, countsLine, countryName, diseaseName, formatDay, sourceLabel, STATUS_LABELS } from './noticeLabels';
import { useSituationData } from './useSituationData';
import { pick as translate, countryLabel } from '../../lib/surveillance-presentation';
import type { Locale, OutbreakFile, OutbreakSummary, WeeklyChange } from '../../utils/situationTypes';
import { monthOfWeek, weekOfDay, monthTotals } from './archive-model';

type WeekDetails = WeeklyChange & { outbreaks?: OutbreakSummary[] };
function ArchiveOutbreak({ outbreak, period, locale }: { outbreak: OutbreakSummary; period: string; locale: Locale }) {
  const [file, setFile] = useState<OutbreakFile>();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const pick = (en: string, zh: string, fr: string) => translate(locale, en, zh, fr);
  const prefix = locale === 'en' ? '' : `/${locale}`;
  const load = () => {
    if (file || loading) return;
    setLoading(true); setError(false);
    situationData.read<OutbreakFile>(`/situation/outbreaks/${encodeURIComponent(outbreak.outbreak_id)}.json`)
      .then(value => { if (value.schema_version !== 'gids.situation_alerts.v1' || !Array.isArray(value.alerts)) throw new Error('Invalid outbreak'); setFile(value); })
      .catch(() => setError(true)).finally(() => setLoading(false));
  };
  return <details className="border border-[rgb(var(--border))] p-4" onToggle={event => { if (event.currentTarget.open) load(); }}>
    <summary className="cursor-pointer font-semibold">{diseaseName(outbreak, locale)} · {(outbreak.country_codes ?? []).map(code => countryLabel({ code }, locale)).join(' · ')}</summary>
    <p className="text-xs mt-2 text-[rgb(var(--text-muted))]">{pick('First reported', '首次报告', 'Premier signalement')} {formatDay(outbreak.first_published, locale)} · <a className="underline" href={`${prefix}/situation/?outbreak=${encodeURIComponent(outbreak.outbreak_id)}`}>{pick('Show on map', '在地图中显示', 'Afficher sur la carte')}</a></p>
    {error && <p role="status">{pick('Details are unavailable.', '详情暂不可用。', 'Détails indisponibles.')} <button className="underline" onClick={load}>{pick('Retry', '重试', 'Réessayer')}</button></p>}
    {loading && <p role="status">{<GIDSLoader language={locale} compact />}</p>}
    <ul className="mt-3 space-y-3">{(file?.alerts ?? []).filter(alert => weekOfDay(alert.published_date) === period).map(alert => <li className="border-t pt-3" key={alert.alert_id}>
      <strong>{alert.admin_area ? `${alert.admin_area}, ` : ''}{countryName(alert, locale)}</strong> · {formatDay(alert.published_date, locale)} · {STATUS_LABELS[alert.status]?.[locale] ?? alert.status}
      {countsLine(alert, locale) && <p>{countsLine(alert, locale)}</p>}
      {alertSummary(alert, locale) && <p className="text-sm">{alertSummary(alert, locale)}</p>}
      {alert.quote && <blockquote className="text-sm italic">{alert.quote}</blockquote>}
      <a className="underline text-sm" href={alert.url} target="_blank" rel="noopener noreferrer">{sourceLabel(alert, locale)}</a>
    </li>)}</ul>
  </details>;
}

function WeekOutbreaks({ period, locale }: { period: string; locale: Locale }) {
  const { data, error, retry } = useSituationData<WeekDetails>(`/situation/weekly/${period}.json`);
  const pick = (en: string, zh: string, fr: string) => translate(locale, en, zh, fr);
  if (!data) return <p role="status">{error ? <>{pick('Weekly details are unavailable.', '本周详情暂不可用。', 'Détails hebdomadaires indisponibles.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></> : <GIDSLoader language={locale} compact />}</p>;
  const index = new Map((data.outbreaks ?? []).map(row => [row.outbreak_id, row]));
  return <>{[
    { name: pick('New outbreaks', '新增疫情', 'Nouvelles flambées'), ids: data.new_outbreaks },
    { name: pick('Outbreaks with updates', '有更新的疫情', 'Flambées mises à jour'), ids: data.updated_outbreaks.filter(id => !data.new_outbreaks.includes(id)) },
  ].map(group => group.ids.length > 0 && <section className="space-y-3" key={group.name}><h3 className="font-semibold">{group.name} · {group.ids.length}</h3>{group.ids.map(id => index.has(id) ? <ArchiveOutbreak key={id} outbreak={index.get(id)!} period={period} locale={locale} /> : <p key={id} className="figure-caption">{pick('Outbreak summary is not yet available.', '该疫情的摘要暂不可用。', 'Le résumé de cette flambée est indisponible.')}</p>)}</section>)}</>;
}

export default function SituationArchive({ locale, mode, period: fixedPeriod, month: fixedMonth }: { locale: Locale; mode: 'weekly' | 'monthly'; period?: string; month?: string }) {
  const { data, version, error, retry } = useSituationData<{ periods: WeeklyChange[] }>('/situation/weekly/index.json');
  const query = typeof window === 'undefined' ? new URLSearchParams() : new URLSearchParams(window.location.search);
  const pick = (en: string, zh: string, fr: string) => translate(locale, en, zh, fr);
  const prefix = locale === 'en' ? '' : `/${locale}`;
  if (!data) return <p role="status">{error ? <>{pick('Situation archive is unavailable.', '态势归档暂不可用。', 'Archive indisponible.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></> : <GIDSLoader language={locale} compact />}</p>;
  const weeks = [...(data.periods ?? [])].filter(row => /^\d{4}-W\d{2}$/.test(row.period_key)).sort((a, b) => b.period_key.localeCompare(a.period_key));
  const month = fixedMonth || query.get('month') || '', period = fixedPeriod || query.get('period') || '';
  const selected = weeks.find(row => row.period_key === period);
  const shown = month ? weeks.filter(row => monthOfWeek(row.period_key) === month) : weeks;
  const months = [...new Set(weeks.map(row => monthOfWeek(row.period_key)))].sort().reverse();
  const keys = mode === 'monthly' ? months : weeks.map(row => row.period_key);
  const active = mode === 'monthly' ? month : period;
  const position = keys.indexOf(active);
  const archiveHref = (value: string) => `${prefix}/situation/${mode}/?${mode === 'monthly' ? 'month' : 'period'}=${value}`;
  const monthlyText = (rows: WeeklyChange[]) => weeklyDiff(monthTotals(rows), locale).replace(/^(This week|本周|Cette semaine)/, pick('This month', '本月', 'Ce mois-ci'));
  return <div className="space-y-5" data-situation-version={version}>
    {active && <nav className="flex flex-wrap justify-between gap-3 text-sm" aria-label={pick('Adjacent periods', '相邻报告期', 'Périodes adjacentes')}>
      {position >= 0 && keys[position + 1] ? <a className="underline" href={archiveHref(keys[position + 1])}>← {keys[position + 1]}</a> : <span />}
      <a className="underline" href={`${prefix}/situation/${mode}/`}>{mode === 'monthly' ? pick('All months', '全部月份', 'Tous les mois') : pick('All weeks', '全部周', 'Toutes les semaines')}</a>
      {position > 0 ? <a className="underline" href={archiveHref(keys[position - 1])}>{keys[position - 1]} →</a> : <span />}
    </nav>}
    {!weeks.length && <p>{pick('No weekly change has been published yet.', '尚未发布每周变化。', 'Aucun changement hebdomadaire publié.')}</p>}
    {month && shown.length > 0 && <div><h2 className="text-xl font-semibold">{month}</h2><p className="mt-2">{monthlyText(shown)}</p></div>}
    {mode === 'monthly' && !month ? <ul className="space-y-3">{months.map(value => { const rows = weeks.filter(row => monthOfWeek(row.period_key) === value); return <li key={value}><a className="site-action-secondary border p-3 block" href={archiveHref(value)}><strong>{value}</strong> · {rows.length} {pick('weeks', '周', 'semaines')}<span className="block text-sm mt-1">{monthlyText(rows)}</span></a></li>; })}</ul> : !selected && <ul className="space-y-3">{shown.map(row => <li key={row.period_key}><a className="site-action-secondary border p-3 block" href={`${prefix}/situation/weekly/?period=${row.period_key}`}><strong>{row.period_key}</strong><span className="block text-sm mt-1">{weeklyDiff(row, locale)}</span></a></li>)}</ul>}
    {selected && <section className="space-y-4"><h2 className="text-xl font-semibold">{selected.period_key}</h2><p>{weeklyDiff(selected, locale)}</p><WeekOutbreaks key={selected.period_key} period={selected.period_key} locale={locale} /></section>}
    {(period && !selected || month && !shown.length) && <p>{pick('This period is absent from the current archive.', '当前归档不包含该报告期。', 'Cette période ne figure pas dans les archives actuelles.')}</p>}
  </div>;
}
