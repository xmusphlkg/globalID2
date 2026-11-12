import GIDSLoader from '../GIDSLoader';
import { useSituationData } from './useSituationData';
import NoticeSnapshots from './NoticeSnapshots';
import { alertSummary, countryName, countsLine, diseaseName, formatDay, sourceLabel, STATUS_LABELS } from './noticeLabels';
import type { Locale, SituationAlertRecord } from '../../utils/situationTypes';

export default function RuntimeNoticePanel({ dataUrl, locale, limit = 8, mapHref, diseaseId, contextCodes = [] }: {
  dataUrl: string; locale: Locale; limit?: number; contextCodes?: string[]; mapHref?: string; diseaseId?: string;
}) {
  const { data, version, error, retry } = useSituationData<{ alerts: SituationAlertRecord[] }>(dataUrl);
  const pick = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  const prefix = locale === 'en' ? '' : `/${locale}`;
  if (!data) return <p className="figure-caption" role="status">{error ? <>{pick('Official alerts are unavailable.', '官方警报暂不可用。', 'Les alertes officielles sont indisponibles.')} <button className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></> : <GIDSLoader language={locale} compact />}</p>;
  const rows = [...(data.alerts ?? [])].filter(row => !diseaseId || row.disease_id?.toUpperCase() === diseaseId.toUpperCase())
    .sort((a, b) => b.published_date.localeCompare(a.published_date) || a.alert_id.localeCompare(b.alert_id)).slice(0, limit);
  return <section className="intl-panel" data-situation-version={version}>
    <div className="intl-panel-header">
      <div>
        <h2 className="figure-title intl-panel-heading">{pick('Official outbreak alerts', '官方疫情通报', 'Alertes officielles de flambée')}</h2>
      </div>
      {mapHref && <a className="intl-panel-action" href={mapHref}>{pick('Open on the situation map', '在态势地图中打开', 'Ouvrir sur la carte de situation')}</a>}
    </div>
    {!rows.length && <p className="figure-caption">{pick('No official outbreak alert is available for this selection.', '此范围暂无官方疫情警报。', 'Aucune alerte officielle pour cette sélection.')}</p>}
    <ul className="intl-panel-list">{rows.map(alert => <li className="intl-panel-item" key={alert.alert_id}>
      <p className="intl-panel-meta">
        <time className="intl-panel-date" dateTime={alert.published_date}>{formatDay(alert.published_date, locale)}</time>
        <span className="intl-panel-source">{sourceLabel(alert, locale)}</span>
        <span className="intl-panel-status">{STATUS_LABELS[alert.status]?.[locale] ?? alert.status}</span>
      </p>
      <a className="intl-panel-title" href={alert.url} target="_blank" rel="noopener noreferrer">{diseaseName(alert, locale)} · {alert.admin_area ? `${alert.admin_area}, ` : ''}{countryName(alert, locale)}</a>
      {countsLine(alert, locale) && <p className="intl-panel-counts">{countsLine(alert, locale)}</p>}
      {alertSummary(alert, locale) && <p className="intl-panel-summary">{alertSummary(alert, locale)}</p>}
      {alert.quote && <blockquote className="intl-panel-quote">{alert.quote}</blockquote>}
      <NoticeSnapshots alert={alert} locale={locale} contextCodes={contextCodes} />
      <div className="intl-panel-actions"><a className="intl-panel-action" href={`${prefix}/situation/?place=${encodeURIComponent(alert.alert_id)}`}>{pick('Show on map', '在地图中显示', 'Afficher sur la carte')}</a></div>
    </li>)}</ul>
  </section>;
}
