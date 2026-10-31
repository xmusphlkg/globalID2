// Full-viewport situation stage. The map fills the page; filters, the place list
// and the joined catalogue sit on top of it. Quotes load with the outbreak timeline.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import './situation.css';
import type { Locale, MapPoint, MonitoringProduct, OutbreakFile, SeriesGlance, SiteIndex, SituationAlertRecord } from '../../utils/situationTypes.ts';
import { ageDays, DEFAULT_FILTERS, RECENCY_COLORS, filterPoints, placeLabel, recencyOf, sortPoints, type Filters, type WindowKey } from './model.ts';
import { COPY, STATUS_LABELS, alertSummary, countryName, countsLine, diseaseName, formatCount, formatDay, formatSignedPercent, sourceLabel } from './labels.ts';
import { seriesKey } from './site.ts';
import { provenance } from './text.ts';
import type { CanvasProps, RegionTint } from './SituationMapCanvas.tsx';

interface ArchiveLinks {
  weekly: string;
  monthly: string;
  methods: string;
}

interface Props {
  locale: Locale;
  asOf: string;
  generatedAt: string;
  points: MapPoint[];
  site: SiteIndex;
  monitoring: MonitoringProduct[];
  activeOutbreaks: number;
  sourcesOk: number;
  sourceCount: number;
  links: ArchiveLinks;
}

type Selection = { kind: 'place'; id: string } | { kind: 'country'; code: string } | null;
type ListMode = 'auto' | 'open' | 'closed';
type Remote = OutbreakFile | 'loading' | 'error';

interface Option {
  id: string;
  label: string;
  count: number;
}

const LIST_LIMIT = 300;
const OUTBREAK_URL = (id: string) => `/situation/outbreaks/${encodeURIComponent(id)}.json`;

function tally(points: MapPoint[], idOf: (point: MapPoint) => string | null, labelOf: (point: MapPoint) => string): Option[] {
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

function Select({ label, value, options, anyLabel, onChange }: { label: string; value: string; options: Option[]; anyLabel: string; onChange: (value: string) => void }) {
  return (
    <label className="sit-field">
      <span>{label}</span>
      <select value={value} onChange={event => onChange(event.target.value)}>
        <option value="">{anyLabel}</option>
        {options.map(option => (
          <option key={option.id} value={option.id}>{option.label} ({option.count})</option>
        ))}
      </select>
    </label>
  );
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = 160;
  const height = 36;
  const path = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - ((value - min) / span) * (height - 4) - 2;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  return (
    <svg className="sit-spark" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <path d={path} />
    </svg>
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

function diseaseIdFromQuery(value: string, site: SiteIndex, points: MapPoint[]): string {
  const upper = value.trim().toUpperCase();
  if (!upper) return '';
  if (site.diseases[upper] || points.some(point => (point.disease_id ?? '').toUpperCase() === upper)) return upper;
  const bySlug = Object.entries(site.diseases).find(([, disease]) => disease.slug === value.trim().toLowerCase());
  return bySlug?.[0] ?? '';
}

export default function SituationExplorer({
  locale,
  asOf,
  generatedAt,
  points,
  site,
  monitoring,
  activeOutbreaks,
  sourcesOk,
  sourceCount,
  links,
}: Props) {
  const copy = COPY[locale];
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selection, setSelection] = useState<Selection>(null);
  const [listMode, setListMode] = useState<ListMode>('auto');
  const [wide, setWide] = useState(true);
  const [outbreaks, setOutbreaks] = useState<Record<string, Remote>>({});
  const [Canvas, setCanvas] = useState<ComponentType<CanvasProps> | null>(null);
  const [canvasFailed, setCanvasFailed] = useState(false);
  const requested = useRef(new Set<string>());
  const skipWrite = useRef(true);

  useEffect(() => {
    let live = true;
    import('./SituationMapCanvas.tsx')
      .then(module => live && setCanvas(() => module.default))
      .catch(() => live && setCanvasFailed(true));
    const query = window.matchMedia('(min-width: 900px)');
    const applyWide = () => setWide(query.matches);
    applyWide();
    query.addEventListener('change', applyWide);

    const params = new URLSearchParams(window.location.search);
    const disease = diseaseIdFromQuery(params.get('disease') ?? '', site, points);
    const countryRaw = (params.get('country') ?? '').toUpperCase();
    const country = /^[A-Z]{2}$/.test(countryRaw) && (site.countries[countryRaw] || points.some(point => point.country_code.toUpperCase() === countryRaw))
      ? countryRaw
      : '';
    const windowParam = params.get('window');
    const nextWindow: WindowKey = windowParam === '7' || windowParam === '90' || windowParam === 'all' || windowParam === '30'
      ? windowParam
      : DEFAULT_FILTERS.window;
    const place = params.get('place');
    const outbreak = params.get('outbreak');
    const match = place
      ? points.find(point => point.alert_id === place) ?? null
      : outbreak
        ? sortPoints(points.filter(point => point.outbreak_id === outbreak), asOf)[0] ?? null
        : null;
    setFilters({
      disease: disease || '',
      country: country && points.some(point => point.country_code.toUpperCase() === country) ? country : '',
      source: '',
      window: match && ageDays(match.published_date, asOf) > 30 ? 'all' : nextWindow,
    });
    if (match) setSelection({ kind: 'place', id: match.alert_id });
    else if (country) setSelection({ kind: 'country', code: country });

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelection(null);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      live = false;
      query.removeEventListener('change', applyWide);
      window.removeEventListener('keydown', onKey);
    };
    // Deep links are read once, on arrival.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (skipWrite.current) {
      skipWrite.current = false;
      return;
    }
    const url = new URL(window.location.href);
    const write = (key: string, value: string) => (value ? url.searchParams.set(key, value) : url.searchParams.delete(key));
    write('place', selection?.kind === 'place' ? selection.id : '');
    write('country', filters.country || (selection?.kind === 'country' ? selection.code : ''));
    write('disease', filters.disease);
    write('window', filters.window === DEFAULT_FILTERS.window ? '' : filters.window);
    url.searchParams.delete('outbreak');
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(null, '', next);
    }
  }, [selection, filters]);

  const visible = useMemo(() => sortPoints(filterPoints(points, filters, asOf), asOf), [points, filters, asOf]);
  const selected = selection?.kind === 'place' ? points.find(point => point.alert_id === selection.id) ?? null : null;
  const selectedCountryCode = selection?.kind === 'country'
    ? selection.code
    : selected?.country_code.toUpperCase() ?? '';
  const countryGlance = selectedCountryCode ? site.countries[selectedCountryCode] : undefined;
  const listOpen = listMode === 'open' || (listMode === 'auto' && wide);

  const diseases = useMemo(() => tally(points, point => point.disease_id ?? point.disease_label, point => diseaseName(point, locale)), [points, locale]);
  const countries = useMemo(() => tally(points, point => point.country_code, point => countryName(point, locale)), [points, locale]);
  const sources = useMemo(() => tally(points, point => point.source_id, point => sourceLabel(point, locale)), [points, locale]);
  const linked = useMemo(
    () => visible.filter(point => point.disease_id && site.series[seriesKey(point.country_code, point.disease_id)]).length,
    [visible, site.series],
  );
  const monitoringRows = useMemo(() => {
    const seen = new Set<string>();
    return monitoring.filter(product => {
      if (seen.has(product.notice_id)) return false;
      seen.add(product.notice_id);
      return true;
    });
  }, [monitoring]);

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
  const timeline = file?.alerts ?? [];
  const placesInOutbreak = useMemo(() => {
    const seen = new Map<string, SituationAlertRecord>();
    for (const alert of timeline) {
      const key = `${alert.country_code}:${alert.admin_area ?? ''}`;
      if (!seen.has(key)) seen.set(key, alert);
    }
    return [...seen.values()];
  }, [timeline]);

  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setFilters(state => ({ ...state, [key]: value }));
  const closeListOnNarrow = () => {
    if (window.matchMedia('(max-width: 899px)').matches) setListMode('closed');
  };
  const selectPlace = (id: string) => {
    setSelection({ kind: 'place', id });
    closeListOnNarrow();
  };
  const selectCountry = (code: string) => {
    setSelection({ kind: 'country', code: code.toUpperCase() });
    closeListOnNarrow();
  };

  const diseaseOn = Boolean(filters.disease && site.diseases[filters.disease]);
  const regions: RegionTint[] = useMemo(() => {
    if (!diseaseOn) {
      return Object.entries(site.countries)
        .filter(([, country]) => country.seriesCount > 0)
        .map(([code, country]) => ({ code, value: country.seriesCount }));
    }
    return Object.entries(site.series).flatMap(([key, glance]) => {
      const [code, diseaseId] = key.split(':');
      return diseaseId === filters.disease ? [{ code, value: glance.value }] : [];
    });
  }, [diseaseOn, filters.disease, site.countries, site.series]);
  const notes = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [code, country] of Object.entries(site.countries)) {
      const name = countryName({ country_code: code, country_name_en: country.name_en, country_name_zh: country.name_zh }, locale);
      if (diseaseOn) {
        const glance = site.series[seriesKey(code, filters.disease)];
        if (!glance) continue;
        const bits = [name, formatCount(glance.value, locale)];
        if (glance.date) bits.push(formatDay(glance.date, locale));
        out[code] = bits.join(' · ');
      } else if (country.seriesCount > 0) {
        out[code] = `${name} · ${copy.trackedDiseases(country.seriesCount)}`;
      }
    }
    return out;
  }, [site.countries, site.series, diseaseOn, filters.disease, locale, copy]);

  const windows: Array<[WindowKey, string]> = [['all', copy.winAll], ['7', copy.win7], ['30', copy.win30], ['90', copy.win90]];
  const filtersActive = filters.disease !== '' || filters.country !== '' || filters.source !== '' || filters.window !== DEFAULT_FILTERS.window;
  const countryPool = selectedCountryCode
    ? points.filter(point => point.country_code.toUpperCase() === selectedCountryCode
      && (!filters.disease || (point.disease_id ?? point.disease_label) === filters.disease)
      && (!filters.source || point.source_id === filters.source))
    : [];
  const countryAlerts = sortPoints(
    filterPoints(countryPool, { ...filters, country: '', disease: '', source: '' }, asOf),
    asOf,
  );
  const countryHasOlder = countryPool.length > countryAlerts.length && filters.window !== 'all';

  return (
    <div className="sit-stage" data-list={listMode === 'auto' ? undefined : listMode}>
      <header className="sit-bar">
        <div className="sit-bar-title">
          <p className="sit-bar-kicker">{copy.surveillanceHeading}</p>
          <h1>{copy.stageTitle}</h1>
        </div>
        <p className="sit-bar-stats">
          <span><strong>{visible.length}</strong> {copy.placesToggle}</span>
          <span><strong>{activeOutbreaks}</strong> {copy.activeLabel}</span>
          <span><strong>{sourcesOk}/{sourceCount}</strong> {copy.source}</span>
          <span>{copy.linked(linked, visible.length)}</span>
          <time dateTime={generatedAt}>{copy.updated} {formatDay(generatedAt, locale)}</time>
        </p>
        <div className="sit-bar-links">
          <button type="button" className="sit-text-btn" aria-pressed={listOpen} onClick={() => setListMode(listOpen ? 'closed' : 'open')}>{copy.placesToggle}</button>
          <a href={links.weekly}>{copy.weekly}</a>
          <a href={links.monthly}>{copy.monthly}</a>
          <a href={links.methods}>{copy.methods}</a>
        </div>
      </header>

      <div className="sit-map-wrap">
        <section className="sit-map" aria-label={copy.mapLabel}>
          {Canvas ? (
            <Canvas
              points={visible}
              asOf={asOf}
              selectedId={selected?.alert_id ?? null}
              highlightCountry={selectedCountryCode || null}
              locale={locale}
              regions={regions}
              notes={notes}
              onSelect={selectPlace}
              onSelectCountry={selectCountry}
            />
          ) : (
            <p className="sit-note" role="status">{canvasFailed ? copy.mapFailed : copy.mapLoading}</p>
          )}
        </section>

        <aside className="sit-panel sit-list" aria-label={copy.filters}>
          <h2>{copy.filters}</h2>
          <Select label={copy.disease} value={filters.disease} options={diseases} anyLabel={copy.any} onChange={value => set('disease', value)} />
          <Select label={copy.country} value={filters.country} options={countries} anyLabel={copy.any} onChange={value => set('country', value)} />
          <Select label={copy.source} value={filters.source} options={sources} anyLabel={copy.any} onChange={value => set('source', value)} />
          <label className="sit-field">
            <span>{copy.window}</span>
            <select value={filters.window} onChange={event => set('window', event.target.value as WindowKey)}>
              {windows.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <button type="button" className="sit-reset" disabled={!filtersActive} onClick={() => setFilters(DEFAULT_FILTERS)}>{copy.reset}</button>
          {filters.country && (
            <button type="button" className="sit-text-btn" onClick={() => selectCountry(filters.country)}>{copy.countryDossier}</button>
          )}
          <div className="sit-legend">
            <h3>{copy.legendHeading}</h3>
            <ul>
              {(['fresh', 'recent', 'older', 'stale'] as const).map(key => (
                <li key={key}>
                  <span className={`sit-dot${key === 'fresh' ? ' is-live' : ''}${key === 'recent' ? ' is-live-slow' : ''}`} style={{ background: RECENCY_COLORS[key] }} aria-hidden="true" />
                  {copy[key]}
                </li>
              ))}
            </ul>
            <p className="sit-muted">{copy.legendNote}</p>
          </div>
          <section aria-labelledby="sit-list-heading">
            <h2 id="sit-list-heading">{copy.listHeading} <small>{copy.places(visible.length)}</small></h2>
            {visible.length === 0 ? (
              <p className="sit-muted">{copy.noResults}</p>
            ) : (
              <ul className="sit-places-list">
                {visible.slice(0, LIST_LIMIT).map(point => {
                  const recency = recencyOf(point.published_date, asOf);
                  return (
                    <li key={point.alert_id}>
                      <button type="button" className={`sit-item${point.alert_id === selected?.alert_id ? ' is-selected' : ''}`} aria-pressed={point.alert_id === selected?.alert_id} onClick={() => selectPlace(point.alert_id)}>
                        <span className={`sit-dot${recency === 'fresh' ? ' is-live' : ''}${recency === 'recent' ? ' is-live-slow' : ''}`} style={{ background: RECENCY_COLORS[recency] }} aria-hidden="true" />
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
          {monitoringRows.length > 0 && (
            <details className="sit-monitor">
              <summary>{copy.monitoringHeading} <small>{monitoringRows.length}</small></summary>
              <p className="sit-muted">{copy.monitoringLead}</p>
              <ul>
                {monitoringRows.map(product => (
                  <li key={product.notice_id}>
                    <a href={product.url} target="_blank" rel="noopener noreferrer">{product.title_en}</a>
                    <span>{sourceLabel(product, locale)} · {formatDay(product.published_date, locale)}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </aside>

        <aside className={`sit-panel sit-detail${selection ? ' is-open' : ''}`} aria-live="polite" aria-labelledby="sit-detail-heading">
          <div className="sit-detail-tools">
            <h2 id="sit-detail-heading">{selected ? copy.detailHeading : copy.countryDossier}</h2>
            {selection && <button type="button" className="sit-text-btn" onClick={() => setSelection(null)}>{copy.close}</button>}
          </div>
          {!selection && <p className="sit-muted">{copy.pickHint}</p>}
          {selection?.kind === 'country' && countryGlance && (
            <CountryDossier
              locale={locale}
              code={selection.code}
              country={countryGlance}
              diseaseId={diseaseOn ? filters.disease : ''}
              series={diseaseOn ? site.series[seriesKey(selection.code, filters.disease)] : undefined}
              disease={diseaseOn ? site.diseases[filters.disease] : undefined}
              alerts={countryAlerts}
              canWiden={countryHasOlder && filters.window !== 'all'}
              onWiden={() => set('window', 'all')}
              onSelect={selectPlace}
            />
          )}
          {selected && (
            <>
              <header className="sit-detail-head">
                <div>
                  <strong>{diseaseName(selected, locale)}</strong>
                  <p>
                    {file
                      ? `${copy.firstReported} ${formatDay(file.first_published, locale)} · ${copy.updated} ${formatDay(file.last_published, locale)} · ${copy.alertsCount(file.alert_count)}`
                      : `${copy.updated} ${formatDay(selected.published_date, locale)}`}
                  </p>
                </div>
              </header>
              <FusionCard locale={locale} point={selected} site={site} />
              {placesInOutbreak.length > 1 && (
                <div className="sit-places">
                  <h3>{copy.placesInOutbreak}</h3>
                  <ul>
                    {placesInOutbreak.map(alert => (
                      <li key={`${alert.country_code}:${alert.admin_area ?? ''}`}>
                        <button type="button" className={alert.country_code === selected.country_code && (alert.admin_area ?? '') === (selected.admin_area ?? '') ? 'is-selected' : ''} onClick={() => {
                          const samePlace = (point: MapPoint) => point.outbreak_id === selected.outbreak_id && point.country_code === alert.country_code;
                          const target = points.find(point => samePlace(point) && (point.admin_area ?? '') === (alert.admin_area ?? ''))
                            ?? points.find(samePlace);
                          if (target) selectPlace(target.alert_id);
                        }}>
                          {placeLabel(alert, countryName(alert, locale))}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {remote === 'loading' && <p className="sit-muted" role="status">{copy.mapLoading}</p>}
              {remote === 'error' && <p className="sit-muted" role="status">{copy.timelineFailed}</p>}
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
        </aside>

        <div className="sit-legend-float">
          <ul>
            {(['fresh', 'recent', 'older', 'stale'] as const).map(key => (
              <li key={key}>
                <span className={`sit-dot${key === 'fresh' ? ' is-live' : ''}${key === 'recent' ? ' is-live-slow' : ''}`} style={{ background: RECENCY_COLORS[key] }} aria-hidden="true" />
                {copy[key]}
              </li>
            ))}
          </ul>
          <p className="sit-muted">{copy.breathe} {diseaseOn ? copy.seriesLegend : copy.footprintLegend}</p>
        </div>
      </div>
      <p className="sit-sr">{copy.legendNote}</p>
    </div>
  );
}

function FusionCard({ locale, point, site }: { locale: Locale; point: MapPoint; site: SiteIndex }) {
  const copy = COPY[locale];
  const disease = point.disease_id ? site.diseases[point.disease_id.toUpperCase()] : undefined;
  const country = site.countries[point.country_code.toUpperCase()];
  const glance = point.disease_id ? site.series[seriesKey(point.country_code, point.disease_id)] : undefined;
  const diseaseLabel = diseaseName(point, locale);
  return (
    <div className="sit-fusion">
      <h3>{copy.surveillanceHeading}</h3>
      {glance ? (
        <>
          <p>{copy.nationalSeries}{glance.date ? ` · ${formatDay(glance.date, locale)}` : ''}</p>
          <p>
            <strong>{formatCount(glance.value, locale)}</strong>
            {glance.changePct != null && ` · ${formatSignedPercent(glance.changePct, locale)} ${copy.vsPrevious}`}
          </p>
          {glance.spark && <Sparkline values={glance.spark} />}
        </>
      ) : country && country.seriesCount > 0 && point.disease_id ? (
        <p>{copy.diseaseNotInSeries(diseaseLabel, country.seriesCount)}</p>
      ) : (
        <p>{copy.noNationalSeries}</p>
      )}
      <div className="sit-chip-row">
        {glance?.curveHref && <a className="sit-chip" href={glance.curveHref}>{copy.localCurve}</a>}
        {country?.href && <a className="sit-chip" href={country.href}>{copy.openCountry}</a>}
        {disease?.href && <a className="sit-chip" href={disease.href}>{copy.openDisease}</a>}
        {disease?.researchHref && disease.researchCount > 0 && <a className="sit-chip" href={disease.researchHref}>{copy.openResearch(disease.researchCount)}</a>}
        {country?.researchHref && country.researchCount > 0 && <a className="sit-chip" href={country.researchHref}>{copy.openResearch(country.researchCount)}</a>}
      </div>
    </div>
  );
}

function CountryDossier({
  locale,
  code,
  country,
  diseaseId,
  series,
  disease,
  alerts,
  canWiden,
  onWiden,
  onSelect,
}: {
  locale: Locale;
  code: string;
  country: SiteIndex['countries'][string];
  diseaseId: string;
  series?: SeriesGlance;
  disease?: SiteIndex['diseases'][string];
  alerts: MapPoint[];
  canWiden: boolean;
  onWiden: () => void;
  onSelect: (id: string) => void;
}) {
  const copy = COPY[locale];
  const name = countryName({ country_code: code, country_name_en: country.name_en, country_name_zh: country.name_zh }, locale);
  return (
    <>
      <header className="sit-detail-head">
        <div>
          <strong>{name}</strong>
          <p>{country.seriesCount > 0 ? copy.trackedDiseases(country.seriesCount) : copy.noNationalSeries}{country.latestDate ? ` · ${formatDay(country.latestDate, locale)}` : ''}</p>
        </div>
      </header>
      {diseaseId && series && (
        <div className="sit-fusion">
          <h3>{disease?.name_zh && locale === 'zh' ? disease.name_zh : disease?.name_en ?? diseaseId}</h3>
          <p>{copy.nationalSeries}{series.date ? ` · ${formatDay(series.date, locale)}` : ''}</p>
          <p>
            <strong>{formatCount(series.value, locale)}</strong>
            {series.changePct != null && ` · ${formatSignedPercent(series.changePct, locale)} ${copy.vsPrevious}`}
          </p>
          {series.spark && <Sparkline values={series.spark} />}
        </div>
      )}
      {diseaseId && !series && country.seriesCount > 0 && disease && (
        <p className="sit-muted">{copy.diseaseNotInSeries(locale === 'zh' && disease.name_zh ? disease.name_zh : disease.name_en, country.seriesCount)}</p>
      )}
      <div className="sit-chip-row">
        {series?.curveHref && <a className="sit-chip" href={series.curveHref}>{copy.localCurve}</a>}
        {country.href && <a className="sit-chip" href={country.href}>{copy.openCountry}</a>}
        {disease?.href && <a className="sit-chip" href={disease.href}>{copy.openDisease}</a>}
        {disease?.researchHref && disease.researchCount > 0 && <a className="sit-chip" href={disease.researchHref}>{copy.openResearch(disease.researchCount)}</a>}
        {country.researchHref && country.researchCount > 0 && <a className="sit-chip" href={country.researchHref}>{copy.openResearch(country.researchCount)}</a>}
      </div>
      <section>
        <h3>{copy.alertsHere}</h3>
        {alerts.length === 0 ? (
          <>
            <p className="sit-muted">{copy.noAlertsHere}</p>
            {canWiden && <button type="button" className="sit-text-btn" onClick={onWiden}>{copy.widenWindow}</button>}
          </>
        ) : (
          <ul className="sit-places-list">
            {alerts.slice(0, 12).map(point => (
              <li key={point.alert_id}>
                <button type="button" className="sit-item" onClick={() => onSelect(point.alert_id)}>
                  <span className="sit-item-main">
                    <strong>{diseaseName(point, locale)}</strong>
                    <span>{formatDay(point.published_date, locale)}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
