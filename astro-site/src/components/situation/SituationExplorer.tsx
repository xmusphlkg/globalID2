// Map-first explorer: filters on the left, map and place list in the middle, the alert
// timeline of the selected outbreak on the right. Everything except the map canvas renders
// on the server, so the list and its links exist without JavaScript.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import './situation.css';
import type { Locale, MapPoint, OutbreakFile, SituationAlertRecord } from '../../utils/situationTypes.ts';
import {
  DEFAULT_FILTERS,
  RECENCY_COLORS,
  filterPoints,
  placeLabel,
  recencyOf,
  sortPoints,
  type Filters,
  type WindowKey,
} from './model.ts';
import {
  COPY,
  STATUS_LABELS,
  alertSummary,
  countryName,
  countsLine,
  diseaseName,
  formatCount,
  formatDay,
  sourceLabel,
} from './labels.ts';
import { provenance } from './text.ts';
import type { CanvasProps } from './SituationMapCanvas.tsx';

interface Props {
  locale: Locale;
  asOf: string;
  points: MapPoint[];
}

type Remote = OutbreakFile | 'loading' | 'error';
interface Option {
  id: string;
  label: string;
  count: number;
}

const LIST_LIMIT = 300;
const OUTBREAK_URL = (id: string) => `/situation/outbreaks/${encodeURIComponent(id)}.json`;

function tally(points: MapPoint[], idOf: (p: MapPoint) => string | null, labelOf: (p: MapPoint) => string): Option[] {
  const counts = new Map<string, Option>();
  for (const point of points) {
    const id = idOf(point);
    if (!id) continue;
    const entry = counts.get(id) ?? { id, label: labelOf(point), count: 0 };
    entry.count += 1;
    counts.set(id, entry);
  }
  return [...counts.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

function Select({
  label,
  value,
  options,
  anyLabel,
  onChange,
}: {
  label: string;
  value: string;
  options: Option[];
  anyLabel: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="sit-field">
      <span>{label}</span>
      <select value={value} onChange={event => onChange(event.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map(option => (
          <option key={option.id} value={option.id}>
            {option.label} ({option.count})
          </option>
        ))}
      </select>
    </label>
  );
}

function AlertItem({ alert, selected, locale }: { alert: SituationAlertRecord; selected: boolean; locale: Locale }) {
  const copy = COPY[locale];
  const counts = countsLine(alert, locale);
  const how = provenance(alert.ai, locale);
  return (
    <li className={`sit-alert${selected ? ' is-selected' : ''}`}>
      <div className="sit-alert-head">
        <time dateTime={alert.published_date}>{formatDay(alert.published_date, locale)}</time>
        <span className={`sit-status sit-status-${alert.status}`}>{STATUS_LABELS[alert.status][locale]}</span>
        <strong>{placeLabel(alert, countryName(alert, locale))}</strong>
      </div>
      {counts ? <p className="sit-counts">{counts}</p> : alert.status !== 'routine' && <p className="sit-muted">{copy.noCounts}</p>}
      {alertSummary(alert, locale) && <p className="sit-summary">{alertSummary(alert, locale)}</p>}
      {alert.quote ? (
        <blockquote className="sit-quote" cite={alert.url}>
          <p>{alert.quote}</p>
          <footer>{copy.quote} · {sourceLabel(alert, locale)}</footer>
        </blockquote>
      ) : (
        <p className="sit-muted">{copy.quoteWithheld}</p>
      )}
      <p className="sit-links">
        <a href={alert.url} target="_blank" rel="noopener noreferrer">{copy.originalReport}</a>
        <span>{copy.sourceLine}: {sourceLabel(alert, locale)}</span>
        {how && <span>{how}</span>}
      </p>
    </li>
  );
}

export default function SituationExplorer({ locale, asOf, points }: Props) {
  const copy = COPY[locale];
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [outbreaks, setOutbreaks] = useState<Record<string, Remote>>({});
  const [Canvas, setCanvas] = useState<ComponentType<CanvasProps> | null>(null);
  const [canvasFailed, setCanvasFailed] = useState(false);
  const requested = useRef(new Set<string>());

  useEffect(() => {
    let live = true;
    import('./SituationMapCanvas.tsx')
      .then(module => live && setCanvas(() => module.default))
      .catch(() => live && setCanvasFailed(true));
    const wanted = new URLSearchParams(window.location.search).get('outbreak');
    if (wanted) {
      const match = sortPoints(points.filter(point => point.outbreak_id === wanted), asOf)[0];
      if (match) setSelectedId(match.alert_id);
    }
    return () => {
      live = false;
    };
    // Runs once: the deep link is read on arrival only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const visible = useMemo(() => sortPoints(filterPoints(points, filters, asOf), asOf), [points, filters, asOf]);
  const selected = useMemo(() => points.find(point => point.alert_id === selectedId) ?? null, [points, selectedId]);

  const diseases = useMemo(() => tally(points, p => p.disease_id ?? p.disease_label, p => diseaseName(p, locale)), [points, locale]);
  const countries = useMemo(() => tally(points, p => p.country_code, p => countryName(p, locale)), [points, locale]);
  const sources = useMemo(() => tally(points, p => p.source_id, p => sourceLabel(p, locale)), [points, locale]);

  const outbreakId = selected?.outbreak_id ?? null;
  useEffect(() => {
    if (!outbreakId || requested.current.has(outbreakId)) return;
    requested.current.add(outbreakId);
    setOutbreaks(state => ({ ...state, [outbreakId]: 'loading' }));
    fetch(OUTBREAK_URL(outbreakId))
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<OutbreakFile>;
      })
      .then(file => setOutbreaks(state => ({ ...state, [outbreakId]: file })))
      .catch(() => setOutbreaks(state => ({ ...state, [outbreakId]: 'error' })));
  }, [outbreakId]);

  const remote = outbreakId ? outbreaks[outbreakId] : undefined;
  const file = remote && typeof remote === 'object' ? remote : null;
  const timeline = file && file.alerts.length ? file.alerts : selected ? [selected] : [];
  const placesInOutbreak = useMemo(() => {
    const seen = new Map<string, SituationAlertRecord>();
    for (const alert of timeline) if (!seen.has(alert.country_code)) seen.set(alert.country_code, alert);
    return [...seen.values()];
  }, [timeline]);

  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setFilters(state => ({ ...state, [key]: value }));
  const pickPlace = (countryCode: string) => {
    const target = points.find(point => point.outbreak_id === outbreakId && point.country_code === countryCode);
    if (target) setSelectedId(target.alert_id);
  };
  const filtersActive = JSON.stringify(filters) !== JSON.stringify(DEFAULT_FILTERS);
  const windows: Array<[WindowKey, string]> = [['all', copy.winAll], ['7', copy.win7], ['30', copy.win30], ['90', copy.win90]];

  return (
    <div className="sit-layout">
      <aside className="sit-filters" aria-label={copy.filters}>
        <h2>{copy.filters}</h2>
        <Select label={copy.disease} value={filters.disease} options={diseases} anyLabel={copy.any} onChange={value => set('disease', value)} />
        <Select label={copy.country} value={filters.country} options={countries} anyLabel={copy.any} onChange={value => set('country', value)} />
        <Select label={copy.source} value={filters.source} options={sources} anyLabel={copy.any} onChange={value => set('source', value)} />
        <label className="sit-field">
          <span>{copy.window}</span>
          <select value={filters.window} onChange={event => set('window', event.target.value as WindowKey)}>
            {windows.map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <button type="button" className="sit-reset" disabled={!filtersActive} onClick={() => setFilters(DEFAULT_FILTERS)}>
          {copy.reset}
        </button>
        <div className="sit-legend">
          <h3>{copy.legendHeading}</h3>
          <ul>
            {(['fresh', 'recent', 'older', 'stale'] as const).map(key => (
              <li key={key}>
                <span className="sit-dot" style={{ background: RECENCY_COLORS[key] }} aria-hidden="true" />
                {copy[key]}
              </li>
            ))}
          </ul>
          <p className="sit-muted">{copy.legendNote}</p>
        </div>
      </aside>

      <div className="sit-main">
        <section className="sit-map" aria-label={copy.mapLabel}>
          {Canvas ? (
            <Canvas points={visible} asOf={asOf} selectedId={selectedId} locale={locale} onSelect={setSelectedId} />
          ) : (
            <p className="sit-note" role="status">{canvasFailed ? copy.mapFailed : copy.mapLoading}</p>
          )}
        </section>

        <section className="sit-list" aria-labelledby="sit-list-heading">
          <h2 id="sit-list-heading">{copy.listHeading} <small>{copy.places(visible.length)}</small></h2>
          {visible.length === 0 ? (
            <p className="sit-muted">{copy.noResults}</p>
          ) : (
            <ul>
              {visible.slice(0, LIST_LIMIT).map(point => {
                const recency = recencyOf(point.published_date, asOf);
                return (
                  <li key={point.alert_id}>
                    <button
                      type="button"
                      className={`sit-item${point.alert_id === selectedId ? ' is-selected' : ''}`}
                      aria-pressed={point.alert_id === selectedId}
                      onClick={() => setSelectedId(point.alert_id)}
                    >
                      <span className="sit-dot" style={{ background: RECENCY_COLORS[recency] }} aria-hidden="true" />
                      <span className="sit-sr">{copy[recency]}</span>
                      <span className="sit-item-main">
                        <strong>{diseaseName(point, locale)}</strong>
                        <span>{placeLabel(point, countryName(point, locale))}</span>
                      </span>
                      <span className="sit-item-meta">
                        <span className={`sit-status sit-status-${point.status}`}>{STATUS_LABELS[point.status][locale]}</span>
                        <time dateTime={point.published_date}>{formatDay(point.published_date, locale)}</time>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>

      <section className="sit-detail" aria-live="polite" aria-labelledby="sit-detail-heading">
        <h2 id="sit-detail-heading">{copy.detailHeading}</h2>
        {!selected ? (
          <p className="sit-muted">{copy.pickHint}</p>
        ) : (
          <>
            <header className="sit-detail-head">
              <strong>{diseaseName(selected, locale)}</strong>
              <span>
                {file
                  ? `${copy.firstReported} ${formatDay(file.first_published, locale)} · ${copy.updated} ${formatDay(file.last_published, locale)} · ${copy.alertsCount(file.alert_count)}`
                  : `${copy.updated} ${formatDay(selected.published_date, locale)}`}
              </span>
            </header>
            {placesInOutbreak.length > 1 && (
              <div className="sit-places">
                <h3>{copy.placesInOutbreak}</h3>
                <ul>
                  {placesInOutbreak.map(alert => (
                    <li key={alert.country_code}>
                      <button
                        type="button"
                        className={alert.country_code === selected.country_code ? 'is-selected' : ''}
                        onClick={() => pickPlace(alert.country_code)}
                      >
                        {countryName(alert, locale)}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {selected.local && (
              <div className="sit-local">
                <h3>{copy.localHeading}</h3>
                <p>
                  {copy.localLatest}: <strong>{formatCount(selected.local.value, locale)}</strong>
                  {selected.local.date ? ` (${formatDay(selected.local.date, locale)})` : ''}
                </p>
                <a href={selected.local.curveHref ?? selected.local.countryHref}>{copy.localCurve}</a>
              </div>
            )}
            {remote === 'loading' && <p className="sit-muted" role="status">{copy.mapLoading}</p>}
            <ol className="sit-timeline">
              {timeline.map(alert => (
                <AlertItem
                  key={alert.alert_id}
                  alert={alert}
                  locale={locale}
                  selected={alert.country_code === selected.country_code && (alert.admin_area ?? '') === (selected.admin_area ?? '')}
                />
              ))}
            </ol>
          </>
        )}
      </section>
    </div>
  );
}
