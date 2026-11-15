import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import type { Locale, SituationAlertRecord } from '../../utils/situationTypes.ts';
import { buildGraph, deduplicateAlerts, filterPoints, type Filters } from './model.ts';
import { COPY, countryName, formatCount } from './labels.ts';
import { AlertItem } from './SituationTimeline.tsx';
import './situation-controls.css';

const PAGE_SIZE = 30;
const TEXT = {
  en: {
    heading: 'All matching events',
    counts: (events: string, reports: string) => `${events} data updates · ${reports} reports`,
    loading: 'Loading matching reports…',
    failed: (_count: string) => 'Some matching reports could not be loaded. Displayed results may be incomplete.',
    empty: 'No alerts match these filters.',
    more: (shown: string, total: string) => `Show more events (${shown} of ${total})`,
  },
  zh: {
    heading: '全部匹配事件',
    counts: (events: string, reports: string) => `${events} 条数据 · ${reports} 份报告`,
    loading: '正在加载匹配报告…',
    failed: (_count: string) => '部分匹配报告加载失败，当前结果可能不完整。',
    empty: '没有符合筛选条件的警报。',
    more: (shown: string, total: string) => `显示更多事件（${shown} / ${total}）`,
  },
  fr: {
    heading: 'Tous les événements correspondants',
    counts: (events: string, reports: string) => `${events} mises à jour · ${reports} rapports`,
    loading: 'Chargement des rapports correspondants…',
    failed: (_count: string) => 'Certains rapports n’ont pas pu être chargés. Les résultats peuvent être incomplets.',
    empty: 'Aucune alerte ne correspond à ces filtres.',
    more: (shown: string, total: string) => `Afficher plus d’événements (${shown} sur ${total})`,
  },
};

interface Props {
  locale: Locale;
  alerts: SituationAlertRecord[];
  filters: Filters;
  asOf: string;
  loading: boolean;
  failed: number;
  onSelect: (alert: SituationAlertRecord) => void;
}

/** Full filter scope, explicitly separate from the currently selected place. */
export default function TimelineOverview({ locale, alerts, filters, asOf, loading, failed, onSelect }: Props) {
  const text = TEXT[locale];
  const copy = COPY[locale];
  const groups = useMemo(() => deduplicateAlerts(filterPoints(alerts.filter(alert => alert.status !== 'routine'), filters, asOf)), [alerts, filters, asOf]);
  const graph = useMemo(() => buildGraph(groups), [groups]);
  const reportCount = useMemo(() => new Set(groups.flatMap(group => group.reports.map(report => report.notice_id || `${report.source_id}:${report.url}`))).size, [groups]);
  const scope = JSON.stringify(filters);
  const [page, setPage] = useState({ scope, limit: PAGE_SIZE });
  const limit = page.scope === scope ? page.limit : PAGE_SIZE;
  const rows = graph.rows.slice(0, limit);
  return (
    <section className="sit-timeline-wrap sit-overview" aria-labelledby="sit-overview-heading" aria-busy={loading}>
      <div className="sit-overview-heading">
        <h3 id="sit-overview-heading">{text.heading}</h3>
        <p className="sit-muted sit-overview-count" aria-live="polite">{text.counts(formatCount(groups.length, locale), formatCount(reportCount, locale))}</p>
      </div>
      {loading && <p className="sit-muted" role="status">{text.loading}</p>}
      {failed > 0 && <p className="sit-overview-error" role="status">{text.failed(formatCount(failed, locale))}</p>}
      {!loading && !failed && !rows.length && <p className="sit-muted" role="status">{text.empty}</p>}
      {graph.lanes.length > 1 && (
        <ul className="sit-lanes">
          {graph.lanes.map(lane => {
            const alert = groups.find(group => group.country_code === lane.key);
            return <li key={lane.key}><i style={{ '--c': lane.color } as CSSProperties} aria-hidden="true" />{lane.other ? copy.otherCountries : alert ? countryName(alert, locale) : lane.key}</li>;
          })}
        </ul>
      )}
      <ol className="sit-timeline">
        {rows.map(row => (
          <AlertItem key={row.alert.alert_id} alert={row.alert} row={row} lanes={graph.lanes} width={graph.width} locale={locale} selected={false} featured={false} showPlace showDisease onSelect={() => onSelect(row.alert)} />
        ))}
      </ol>
      {graph.rows.length > limit && (
        <button type="button" className="sit-text-btn sit-more" onClick={() => setPage({ scope, limit: limit + PAGE_SIZE })}>
          {text.more(formatCount(Math.min(limit, graph.rows.length), locale), formatCount(graph.rows.length, locale))}
        </button>
      )}
    </section>
  );
}
