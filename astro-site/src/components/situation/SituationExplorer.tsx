import { applyDeepLinks, tally, type Selection, type Option } from './explorer-state.ts';
import { AlertItem, Figures } from './SituationTimeline.tsx';
import { FusionCard, CountryDossier } from './CountryDossier.tsx';
import GIDSLoader from '../GIDSLoader';
// Full-viewport situation stage. The map fills the page; filters, the place list
// and the joined catalogue sit on top of it. Quotes load with the outbreak timeline.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType, CSSProperties } from 'react';
import './situation.css';
import { situationData } from '../../lib/situation-data';
import type { Locale, MapPoint, MonitoringProduct, OutbreakFile, SiteIndex, SituationAlertRecord, SituationMapDataFile } from '../../utils/situationTypes.ts';
import { ageDays, buildGraph, DEFAULT_FILTERS, RECENCY_COLORS, deduplicateAlerts, deduplicatePoints, filterPoints, placeLabel, recencyOf, sortPoints, visibleSituationPoints, type Filters, type WindowKey } from './model.ts';
import { COPY, STATUS_LABELS, countryName, diseaseName, formatCount, formatDay, sourceLabel } from './labels.ts';
import { seriesKey } from './site.ts';
import type { CanvasProps, RegionTint } from './SituationMapCanvas.tsx';
import { useSurveillanceSummary } from '../public/useSurveillanceSummary';
import { surveillanceData } from '../../lib/surveillance-data.ts';
import { currentSurveillanceSite } from './currentSurveillanceSite.ts';

interface ArchiveLinks {
  weekly: string;
  monthly: string;
  methods: string;
}

interface Props {
  locale: Locale;
  asOf: string;
  generatedAt: string;
  /** Static JSON with markers and monitoring; kept off the HTML props for the gzip budget. */
  dataUrl: string;
  site: SiteIndex;
  links: ArchiveLinks;
}
type ListMode = 'auto' | 'open' | 'closed';
type Remote = OutbreakFile | 'loading' | 'error';
type DataState = 'loading' | 'ready' | 'error';

const LIST_LIMIT = 300;
const TIMELINE_PAGE = 30;
const OUTBREAK_URL = (id: string) => `/situation/outbreaks/${encodeURIComponent(id)}.json`;

function Select({ label, value, options, anyLabel, onChange }: { label: string; value: string; options: Option[]; anyLabel: string; onChange: (value: string) => void }) {
  return (
    <label className="sit-field">
      <span>{label}</span>
      <select value={value} onChange={event => onChange(event.target.value)}>
        <option value="">{anyLabel}</option>
        {value && !options.some(option => option.id === value) && <option value={value}>{value}</option>}
        {options.map(option => (
          <option key={option.id} value={option.id}>{option.label} ({option.count})</option>
        ))}
      </select>
    </label>
  );
}

export default function SituationExplorer({
  locale,
  asOf: initialAsOf,
  generatedAt: initialGeneratedAt,
  dataUrl,
  site: contentSite,
  links,
}: Props) {
  const copy = COPY[locale];
  const [asOf, setAsOf] = useState(initialAsOf);
  const [generatedAt, setGeneratedAt] = useState(initialGeneratedAt);
  const [points, setPoints] = useState<MapPoint[]>([]);
  const [monitoring, setMonitoring] = useState<MonitoringProduct[]>([]);
  const [activeOutbreaks, setActiveOutbreaks] = useState(0);
  const [sourcesOk, setSourcesOk] = useState(0);
  const [sourceCount, setSourceCount] = useState(0);
  const [dataState, setDataState] = useState<DataState>('loading');
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [selection, setSelection] = useState<Selection>(null);
  const currentMeta = useSurveillanceSummary('/site-data/meta.json');
  const [surveillanceSummaries, setSurveillanceSummaries] = useState<Record<string, Record<string, any>>>({});
  const summaryRequests = useRef(new Set<string>());
  const summaryMounted = useRef(true);
  useEffect(() => { summaryMounted.current = true; return () => { summaryMounted.current = false; }; }, []);
  const site = useMemo(() => currentSurveillanceSite(contentSite, currentMeta.data, surveillanceSummaries), [contentSite, currentMeta.data, surveillanceSummaries]);
  const [listMode, setListMode] = useState<ListMode>('auto');
  const [wide, setWide] = useState(true);
  const [outbreaks, setOutbreaks] = useState<Record<string, Remote>>({});
  const [timelineShown, setTimelineShown] = useState<Record<string, number>>({});
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
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelection(null);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      live = false;
      query.removeEventListener('change', applyWide);
      window.removeEventListener('keydown', onKey);
    };
  }, []);

  useEffect(() => {
    if (!currentMeta.data && !currentMeta.error) return;
    let live = true;
    setDataState('loading');
    situationData.read<SituationMapDataFile>(dataUrl)
      .then(payload => {
        if (!live) return;
        if (payload.schema_version !== 'gids.situation_alerts.v1' || !Array.isArray(payload.points)) throw new Error('Invalid situation map');
        const nextPoints = Array.isArray(payload.points) ? payload.points : [];
        const nextAsOf = payload.as_of || initialAsOf;
        setPoints(nextPoints);
        setMonitoring(Array.isArray(payload.monitoring) ? payload.monitoring : []);
        setActiveOutbreaks(Number(payload.active_outbreaks) || 0);
        setSourcesOk(Number(payload.sources_ok) || 0);
        setSourceCount(Number(payload.source_count) || 0);
        setAsOf(nextAsOf);
        setGeneratedAt(payload.generated_at || initialGeneratedAt);
        const deep = applyDeepLinks(nextPoints, site, nextAsOf);
        setFilters(deep.filters);
        setSelection(deep.selection);
        setDataState('ready');
      })
      .catch(() => {
        if (!live) return;
        setDataState('error');
      });
    return () => {
      live = false;
    };
    // Resolve the current geography catalogue before validating shared links.
    // Disease summary responses must not reapply the reader's initial filters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataUrl, currentMeta.data, currentMeta.error]);

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
    write('source', filters.source);
    write('window', filters.window === DEFAULT_FILTERS.window ? '' : filters.window);
    url.searchParams.delete('outbreak');
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(null, '', next);
    }
  }, [selection, filters]);

  const visible = useMemo(() => visibleSituationPoints(points, asOf, filters), [points, filters, asOf]);
  const selected = selection?.kind === 'place' ? visible.find(point => point.alert_id === selection.id) ?? null : null;
  const selectedDisease = selected?.disease_id;
  useEffect(() => {
    for (const rawId of new Set([filters.disease, selectedDisease].filter(Boolean))) {
      const id = String(rawId).toUpperCase();
      if (!/^D\d+$/.test(id) || summaryRequests.current.has(id)) continue;
      summaryRequests.current.add(id);
      surveillanceData.read<Record<string, any>>(`/site-data/diseases/${id.toLowerCase()}-summary.json`)
        .then(summary => { if (summaryMounted.current) setSurveillanceSummaries(previous => ({ ...previous, [id]: summary })); })
        .catch(() => summaryRequests.current.delete(id));
    }
  }, [filters.disease, selectedDisease]);
  const selectedCountryCode = selection?.kind === 'country'
    ? selection.code
    : selected?.country_code.toUpperCase() ?? '';
  const countryGlance = selectedCountryCode ? site.countries[selectedCountryCode] : undefined;
  const listOpen = listMode === 'open' || (listMode === 'auto' && wide);

  const diseases = useMemo(() => tally(deduplicatePoints(points), point => point.disease_id ?? point.disease_label, point => diseaseName(point, locale)), [points, locale]);
  const countries = useMemo(() => tally(deduplicatePoints(points), point => point.country_code, point => countryName(point, locale)), [points, locale]);
  const sources = useMemo(() => tally(points, point => point.source_id, point => sourceLabel(point, locale)), [points, locale]);
  const linked = useMemo(
    () => visible.filter(point => point.disease_id && site.series[seriesKey(point.country_code, point.disease_id)]).length,
    [visible, site.series],
  );
  const linksKnown = visible.every(point => !point.disease_id || surveillanceSummaries[String(point.disease_id).toUpperCase()]);
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
    situationData.read<OutbreakFile>(OUTBREAK_URL(outbreakId))
      .then(file => setOutbreaks(state => ({ ...state, [outbreakId]: file })))
      .catch(() => setOutbreaks(state => ({ ...state, [outbreakId]: 'error' })));
  }, [outbreakId]);

  const remote = outbreakId ? outbreaks[outbreakId] : undefined;
  const file = remote && typeof remote === 'object' ? remote : null;
  const timeline = useMemo(
    () => deduplicateAlerts(filterPoints(file?.alerts ?? [], filters, asOf), selected?.alert_id),
    [file, filters, asOf, selected?.alert_id],
  );
  const outbreakAlertCount = useMemo(() => deduplicateAlerts(file?.alerts ?? []).length, [file]);
  const placesInOutbreak = useMemo(() => {
    const seen = new Map<string, SituationAlertRecord>();
    for (const alert of timeline) {
      const key = `${alert.country_code}:${alert.admin_area ?? ''}`;
      if (!seen.has(key)) seen.set(key, alert);
    }
    return [...seen.values()];
  }, [timeline]);

  const graph = useMemo(() => buildGraph(timeline, selected?.country_code ?? ''), [timeline, selected?.country_code]);
  const laneNames = useMemo(() => graph.lanes.map(lane => {
    if (lane.other) return copy.otherCountries;
    const alert = timeline.find(item => item.country_code === lane.key);
    return alert ? countryName(alert, locale) : lane.key;
  }), [graph, timeline, locale, copy]);

  const headline = useMemo(() => {
    if (!selected) return null;
    const samePlace = (alert: SituationAlertRecord) =>
      alert.country_code === selected.country_code && (alert.admin_area ?? '') === (selected.admin_area ?? '');
    return timeline.find(alert => alert.alert_id === selected.alert_id) ?? timeline.find(samePlace) ?? null;
  }, [timeline, selected]);
  const selectedRecency = selected ? recencyOf(selected.published_date, asOf) : 'stale';
  const headlineIndex = headline ? graph.rows.findIndex(row => row.alert.alert_id === headline.alert_id) : -1;
  const rowLimit = Math.max((outbreakId ? timelineShown[outbreakId] : 0) || TIMELINE_PAGE, headlineIndex + 1);
  const visibleRows = graph.rows.slice(0, rowLimit);

  const applyFilters = (next: Filters) => {
    setFilters(next);
    setTimelineShown({});
    if (selection?.kind === 'place') {
      const candidates = deduplicatePoints(sortPoints(filterPoints(points, next, asOf), asOf));
      const target = candidates.find(point => point.alert_id === selection.id)
        ?? candidates.find(point => point.outbreak_id === selected?.outbreak_id)
        ?? candidates[0];
      setSelection(target ? { kind: 'place', id: target.alert_id } : null);
    } else if (selection?.kind === 'country') {
      setSelection(next.country ? { kind: 'country', code: next.country } : null);
    }
  };
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => applyFilters({ ...filters, [key]: value });
  const closeListOnNarrow = () => {
    if (window.matchMedia('(max-width: 899px)').matches) setListMode('closed');
  };
  const selectPlace = (id: string) => {
    setSelection({ kind: 'place', id });
    closeListOnNarrow();
  };
  const selectCountry = (code: string) => {
    setFilters(state => ({ ...state, country: code.toUpperCase() }));
    setTimelineShown({});
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

  const detailTitle = selected
    ? diseaseName(selected, locale)
    : selection?.kind === 'country'
      ? countryGlance
        ? countryName({ country_code: selection.code, country_name_en: countryGlance.name_en, country_name_zh: countryGlance.name_zh }, locale)
        : selection.code
      : copy.detailHeading;
  const detailSubtitle = selected
    ? placeLabel(selected, countryName(selected, locale))
    : selection?.kind === 'country' && countryGlance
      ? [countryGlance.seriesCount > 0 ? copy.trackedDiseases(countryGlance.seriesCount) : '', countryGlance.latestDate ? formatDay(countryGlance.latestDate, locale) : ''].filter(Boolean).join(' · ')
      : '';

  const windows: Array<[WindowKey, string]> = [['all', copy.winAll], ['7', copy.win7], ['30', copy.win30], ['90', copy.win90]];
  const filtersActive = filters.disease !== '' || filters.country !== '' || filters.source !== '' || filters.window !== DEFAULT_FILTERS.window;
  const countryPool = selectedCountryCode
    ? points.filter(point => point.country_code.toUpperCase() === selectedCountryCode
      && (!filters.disease || (point.disease_id ?? point.disease_label) === filters.disease)
      && (!filters.source || point.source_id === filters.source))
    : [];
  const countryAlerts = deduplicatePoints(sortPoints(
    filterPoints(countryPool, { ...filters, country: '', disease: '', source: '' }, asOf),
    asOf,
  ));
  const countryHasOlder = filters.window !== 'all' && countryPool.some(point => ageDays(point.published_date, asOf) > Number(filters.window));

  const filterControls = (
    <div className="sit-filter-grid">
      <Select label={copy.disease} value={filters.disease} options={diseases} anyLabel={copy.any} onChange={value => set('disease', value)} />
      <Select label={copy.country} value={filters.country} options={countries} anyLabel={copy.any} onChange={value => set('country', value)} />
      <Select label={copy.source} value={filters.source} options={sources} anyLabel={copy.any} onChange={value => set('source', value)} />
      <label className="sit-field">
        <span>{copy.window}</span>
        <select value={filters.window} onChange={event => set('window', event.target.value as WindowKey)}>
          {windows.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
    </div>
  );

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
          <span>{linksKnown ? copy.linked(linked, visible.length) : locale === 'zh' ? '选择地点查看监测背景' : locale === 'fr' ? 'Choisissez un lieu pour le contexte de surveillance' : 'Select a place for surveillance context'}</span>
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
          {dataState === 'error' ? (
            <p className="sit-note" role="status">{copy.dataFailed}</p>
          ) : dataState !== 'ready' ? (
            <div className="sit-note"><GIDSLoader language={locale} label={copy.mapLoading} /></div>
          ) : Canvas ? (
            <Canvas
              points={visible}
              asOf={asOf}
              selectedId={selected?.alert_id ?? null}
              highlightCountry={selectedCountryCode || null}
              locale={locale}
              regions={regions}
              notes={notes}
              insetLeftRem={wide && listOpen ? 22.6 : 0}
              onSelect={selectPlace}
              onSelectCountry={selectCountry}
            />
          ) : (
            <div className="sit-note">{canvasFailed ? <p role="status">{copy.mapFailed}</p> : <GIDSLoader language={locale} label={copy.mapLoading} />}</div>
          )}
        </section>

        <aside className="sit-panel sit-list" aria-label={copy.filters}>
          <div className="sit-panel-head">
            <h2>{copy.filters}</h2>
            <button type="button" className="sit-reset" disabled={!filtersActive} onClick={() => applyFilters(DEFAULT_FILTERS)}>{copy.reset}</button>
          </div>
          {filterControls}
          {filters.country && (
            <button type="button" className="sit-text-btn" onClick={() => selectCountry(filters.country)}>{copy.countryDossier}</button>
          )}
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
          <div className="sit-detail-bar">
            {selected && (
              <>
                <span className={`sit-dot${selectedRecency === 'fresh' ? ' is-live' : ''}${selectedRecency === 'recent' ? ' is-live-slow' : ''}`} style={{ background: RECENCY_COLORS[selectedRecency] }} aria-hidden="true" />
                <span className="sit-sr">{copy[selectedRecency]}</span>
              </>
            )}
            <div className="sit-detail-title">
              <h2 id="sit-detail-heading">{detailTitle}</h2>
              {detailSubtitle && <p>{detailSubtitle}</p>}
            </div>
            {selected && <span className={`sit-status sit-status-${selected.status}`}>{STATUS_LABELS[selected.status][locale]}</span>}
            {selection && <button type="button" className="sit-close" onClick={() => setSelection(null)} aria-label={copy.close} title={copy.close}>×</button>}
          </div>
          {!selection && <p className="sit-muted">{copy.pickHint}</p>}
          {selection?.kind === 'country' && countryGlance && (
            <CountryDossier
              locale={locale}
              country={countryGlance}
              diseaseId={diseaseOn ? filters.disease : ''}
              series={diseaseOn ? site.series[seriesKey(selection.code, filters.disease)] : undefined}
              disease={diseaseOn ? site.diseases[filters.disease] : undefined}
              alerts={countryAlerts}
              asOf={asOf}
              canWiden={countryHasOlder && filters.window !== 'all'}
              onWiden={() => set('window', 'all')}
              onSelect={selectPlace}
            />
          )}
          {selected && (
            <>
              <p className="sit-detail-meta">
                {file ? (
                  <>
                    <span>{copy.firstReported} <time dateTime={file.first_published}>{formatDay(file.first_published, locale)}</time></span>
                    <span>{copy.updated} <time dateTime={file.last_published}>{formatDay(file.last_published, locale)}</time></span>
                    <span>{copy.alertsCount(outbreakAlertCount)}</span>
                  </>
                ) : (
                  <span>{copy.updated} <time dateTime={selected.published_date}>{formatDay(selected.published_date, locale)}</time></span>
                )}
              </p>
              {headline && <Figures alert={headline} locale={locale} />}
              <FusionCard locale={locale} point={selected} site={site} />
              {placesInOutbreak.length > 1 && (
                <details className="sit-places">
                  <summary>{copy.placesInOutbreak} <small>{placesInOutbreak.length}</small></summary>
                  <ul>
                    {placesInOutbreak.map(alert => (
                      <li key={`${alert.country_code}:${alert.admin_area ?? ''}`}>
                        <button type="button" className={alert.country_code === selected.country_code && (alert.admin_area ?? '') === (selected.admin_area ?? '') ? 'is-selected' : ''} onClick={() => {
                          const samePlace = (point: MapPoint) => point.outbreak_id === selected.outbreak_id && point.country_code === alert.country_code;
                          const target = visible.find(point => samePlace(point) && (point.admin_area ?? '') === (alert.admin_area ?? ''))
                            ?? visible.find(samePlace);
                          if (target) selectPlace(target.alert_id);
                        }}>
                          {placeLabel(alert, countryName(alert, locale))}
                        </button>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {remote === 'loading' && <GIDSLoader language={locale} compact label={copy.mapLoading} />}
              {remote === 'error' && <p className="sit-muted" role="status">{copy.timelineFailed}</p>}
              {file && (
                <section className="sit-timeline-wrap" aria-labelledby="sit-timeline-heading">
                  <h3 id="sit-timeline-heading">{copy.detailHeading} <small>{copy.alertsCount(timeline.length)}</small></h3>
                  {filterControls}
                  {timeline.length === 0 && <p className="sit-muted">{copy.noResults}</p>}
                  {graph.lanes.length > 1 && (
                    <ul className="sit-lanes">
                      {graph.lanes.map((lane, index) => (
                        <li key={lane.key}><i style={{ '--c': lane.color } as CSSProperties} aria-hidden="true" />{laneNames[index]}</li>
                      ))}
                    </ul>
                  )}
                  <ol className="sit-timeline">
                    {visibleRows.map(row => (
                      <AlertItem
                        key={`${selected.alert_id}:${row.alert.alert_id}`}
                        alert={row.alert}
                        row={row}
                        lanes={graph.lanes}
                        width={graph.width}
                        locale={locale}
                        featured={row.alert.alert_id === headline?.alert_id}
                        showPlace={placesInOutbreak.length > 1}
                        selected={row.alert.country_code === selected.country_code && (row.alert.admin_area ?? '') === (selected.admin_area ?? '')}
                      />
                    ))}
                  </ol>
                  {graph.rows.length > rowLimit && (
                    <button type="button" className="sit-text-btn sit-more" onClick={() => outbreakId && setTimelineShown(state => ({ ...state, [outbreakId]: rowLimit + TIMELINE_PAGE }))}>
                      {copy.showEarlier(rowLimit, graph.rows.length)}
                    </button>
                  )}
                </section>
              )}
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
          {diseaseOn && <p className="sit-muted">{copy.seriesLegend}</p>}
        </div>
      </div>
    </div>
  );
}
