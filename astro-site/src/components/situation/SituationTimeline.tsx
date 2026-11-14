import type { CSSProperties } from 'react';
import type { Locale, SituationAlertRecord } from '../../utils/situationTypes.ts';
import { placeLabel, type AlertGroup, type GraphRow, type Lane } from './model.ts';
import { COPY, STATUS_LABELS, alertSummary, basisLabel, countryName, countsLine, formatCount, formatDay, sourceLabel } from './labels.ts';


export function shortCounts(alert: SituationAlertRecord, locale: Locale): string {
  const copy = COPY[locale];
  const parts: string[] = [];
  if (alert.cases != null) parts.push(`${formatCount(alert.cases, locale)} ${copy.statCases.toLocaleLowerCase(locale)}`);
  if (alert.deaths != null) parts.push(`${formatCount(alert.deaths, locale)} ${copy.statDeaths.toLocaleLowerCase(locale)}`);
  return parts.join(' · ');
}

export function Figures({ alert, locale }: { alert: SituationAlertRecord; locale: Locale }) {
  const copy = COPY[locale];
  const tiles: Array<{ key: string; label: string; value: string; note: string }> = [];
  if (alert.cases != null) {
    tiles.push({ key: 'cases', label: copy.statCases, value: formatCount(alert.cases, locale), note: alert.count_basis ? basisLabel(alert.count_basis, locale) : '' });
  }
  if (alert.deaths != null) tiles.push({ key: 'deaths', label: copy.statDeaths, value: formatCount(alert.deaths, locale), note: '' });
  if (tiles.length && alert.as_of) tiles.push({ key: 'asof', label: copy.statAsOf, value: formatDay(alert.as_of, locale), note: '' });
  if (!tiles.length) return alert.status !== 'routine' ? <p className="sit-muted">{copy.noCounts}</p> : null;
  return (
    <dl className="sit-stats">
      {tiles.map(tile => (
        <div key={tile.key} className={`sit-stat sit-stat-${tile.key}`}>
          <dt>{tile.label}</dt>
          <dd>
            <strong>{tile.value}</strong>
            {tile.note && <small>{tile.note}</small>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export const GRAPH_COL = 9;

export const GRAPH_DOT_Y = 15;

export const GRAPH_BEND = 10;

export function GraphCell({ row, lanes, width }: { row: GraphRow<SituationAlertRecord>; lanes: Lane[]; width: number }) {
  const x = (col: number) => col * GRAPH_COL + GRAPH_COL / 2;
  const px = width * GRAPH_COL;
  const color = (lane: number) => lanes[lane].color;
  return (
    <div className="sit-graph" style={{ width: px, '--bend': `${GRAPH_BEND}px` } as CSSProperties} aria-hidden="true">
      <svg className="sit-graph-top" width={px}>
        {row.lines.map(line => {
          const y1 = line.part === 'down' ? GRAPH_DOT_Y : 0;
          const y2 = line.part === 'up' ? GRAPH_DOT_Y : '100%';
          return <line key={`t${line.lane}`} x1={x(line.from)} x2={x(line.from)} y1={y1} y2={y2} stroke={color(line.lane)} />;
        })}
        <circle className="sit-graph-node" cx={x(row.node)} cy={GRAPH_DOT_Y} r={3.5} fill={color(row.lane)} />
      </svg>
      <svg className="sit-graph-bend" width={px} height={GRAPH_BEND}>
        {row.lines.filter(line => line.part !== 'up').map(line => (
          <line key={`b${line.lane}`} x1={x(line.from)} x2={x(line.to)} y1={0} y2={GRAPH_BEND} stroke={color(line.lane)} />
        ))}
      </svg>
    </div>
  );
}

export function AlertItem({ alert, row, lanes, width, selected, featured, showPlace, locale }: { alert: AlertGroup; row: GraphRow<SituationAlertRecord>; lanes: Lane[]; width: number; selected: boolean; featured: boolean; showPlace: boolean; locale: Locale }) {
  const copy = COPY[locale];
  const short = shortCounts(alert, locale);
  const summary = alertSummary(alert, locale);
  return (
    <li className={`sit-alert${selected ? ' is-selected' : ''}`} data-alert-id={alert.alert_id} data-country={alert.country_code}>
      <GraphCell row={row} lanes={lanes} width={width} />
      <details open={featured}>
        <summary>
          <span className="sit-alert-head">
            {showPlace && <span className="sit-alert-place">{placeLabel(alert, countryName(alert, locale))}</span>}
            <span className={`sit-status sit-status-${alert.status}`}>{STATUS_LABELS[alert.status][locale]}</span>
          </span>
          <span className="sit-alert-sub">
            <time dateTime={alert.published_date}>{formatDay(alert.published_date, locale)}</time>
            {short && <> · <span className="sit-alert-counts">{short}</span></>}
          </span>
        </summary>
        <div className="sit-alert-body">
          {!featured && countsLine(alert, locale) && <p className="sit-counts">{countsLine(alert, locale)}</p>}
          {!featured && !short && alert.status !== 'routine' && <p className="sit-muted">{copy.noCounts}</p>}
          {summary && <p className="sit-summary">{summary}</p>}
          {alert.quote ? (
            <blockquote className="sit-quote" cite={alert.url}>
              <p>{alert.quote}</p>
            </blockquote>
          ) : (
            <p className="sit-muted">{copy.quoteWithheld}</p>
          )}
          {alert.reports.filter((report, index, reports) => reports.findIndex(item => item.source_id === report.source_id && item.url === report.url) === index).map(report => (
            <p className="sit-links" key={report.alert_id}>
              <a className="sit-source-link" href={report.url} target="_blank" rel="noopener noreferrer">{copy.originalReport} ↗</a>
              <span>{sourceLabel(report, locale)}</span>
            </p>
          ))}
        </div>
      </details>
    </li>
  );
}
