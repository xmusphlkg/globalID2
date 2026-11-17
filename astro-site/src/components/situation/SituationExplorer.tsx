import { applyDeepLinks, tally, type Selection } from './explorer-state.ts';
import { AlertItem, Figures } from './SituationTimeline.tsx';
import SearchableFilter from './SearchableFilter.tsx';
import MonitoringProducts from './MonitoringProducts.tsx';
import ActiveSituationFilters from './ActiveSituationFilters.tsx';
import TimelineOverview from './TimelineOverview.tsx';
import { useSituationOverview } from './useSituationOverview.ts';
import { useSituationPanelScroll } from './useSituationPanelScroll.ts';
import { FusionCard, CountryDossier } from './CountryDossier.tsx';
import GIDSLoader from '../GIDSLoader';
// Full-viewport situation stage. The map fills the page; filters, the place list
// and the joined catalogue sit on top of it. Quotes load with the outbreak timeline.
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType, CSSProperties } from 'react';
import './situation.css';
import { situationData } from '../../lib/situation-data';
import type { HostType, Locale, MapPoint, MonitoringProduct, OutbreakFile, SiteIndex, SituationAlertRecord, SituationMapDataFile } from '../../utils/situationTypes.ts';
import { ageDays, buildGraph, DEFAULT_FILTERS, RECENCY_COLORS, deduplicateAlerts, deduplicatePoints, latestMatchingPlaces, situationPlaceKey, filterPoints, placeLabel, recencyOf, sortPoints, visibleSituationPoints, type Filters, type WindowKey } from './model.ts';
import { COPY, HOST_FILTER_LABEL, HOST_LABELS, STATUS_LABELS, countryName, diseaseName, formatCount, formatDay, hostLabel, sourceLabel } from './labels.ts';
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
  const [completeIndex, setCompleteIndex] = useState(false);
  const [focusedAlert, setFocusedAlert] = useState<SituationAlertRecord | null>(null);
  const [timelineScope, setTimelineScope] = useState<'place' | 'all'>('place');
  const [monitoring, setMonitoring] = useState<MonitoringProduct[]>([]);
  const [activeOutbreaks, setActiveOutbreaks] = useState(0);
  const [sourcesOk, setSourcesOk] = useState(0);
  const [sourceCount, setSourceCount] = useState(0);
  const [dataState, setDataState] = useState<DataState>('loading');
  const [dataAttempt, setDataAttempt] = useState(0);
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
  const [outbreakAttempt, setOutbreakAttempt] = useState(0);
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
      if (event.key === 'Escape') { setSelection(null); setTimelineScope('place'); }
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
        const indexed = Array.isArray(payload.filter_points);
        const nextPoints = indexed ? payload.filter_points! : payload.points;
        setCompleteIndex(indexed);
        const nextAsOf = payload.as_of || initialAsOf;
        setPoints(nextPoints);
        setMonitoring(Array.isArray(payload.monitoring) ? payload.monitoring : []);
        setActiveOutbreaks(Number(payload.active_outbreaks) || 0);
        setSourcesOk(Number(payload.sources_ok) || 0);
        setSourceCount(Number(payload.source_count) || 0);
        setAsOf(nextAsOf);
        setGeneratedAt(payload.generated_at || initialGeneratedAt);
        const deep = applyDeepLinks(nextPoints, site, nextAsOf, payload.monitoring ?? [], indexed);
        setFilters(deep.filters);
        setSelection(deep.selection);
        setTimelineScope(new URLSearchParams(window.location.search).get('timeline') === 'all' ? 'all' : 'place');
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
  }, [dataUrl, currentMeta.data, currentMeta.error, dataAttempt]);

  useEffect(() => {
    if (skipWrite.current) {
      skipWrite.current = false;
      return;
    }
    const url = new URL(window.location.href);
    const write = (key: string, value: string) => (value ? url.searchParams.set(key, value) : url.searchParams.delete(key));
    write('timeline', timelineScope === 'all' ? 'all' : '');
    write('place', selection?.kind === 'place' ? selection.id : '');
    write('country', filters.country || (selection?.kind === 'country' ? selection.code : ''));
    write('disease', filters.disease);
    write('source', filters.source);
    write('host', filters.host || '');
    write('window', filters.window === DEFAULT_FILTERS.window ? '' : filters.window);
    url.searchParams.delete('outbreak');
    const next = `${url.pathname}${url.search}${url.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(null, '', next);
    }
  }, [selection, filters, timelineScope]);

  const visible = useMemo(() => completeIndex ? latestMatchingPlaces(points, asOf, filters) : visibleSituationPoints(points, asOf, filters), [points, filters, asOf, completeIndex]);
  const selectedRaw = selection?.kind === 'place' ? points.find(point => point.alert_id === selection.id)
    ?? (focusedAlert?.alert_id === selection.id ? focusedAlert : null) : null;
  const selected = selectedRaw && filterPoints([selectedRaw], filters, asOf).length ? selectedRaw : null;
  const selectedMarker = selected ? visible.find(point => situationPlaceKey(point) === situationPlaceKey(selected)) ?? selected : null;
  const overview = useSituationOverview(timelineScope === 'all' && dataState === 'ready', points, filters, asOf);
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
  const detailOpen = Boolean(selection) || timelineScope === 'all';
  const leftScroll = useSituationPanelScroll<HTMLElement>(listOpen, 'filters');
  const rightScroll = useSituationPanelScroll<HTMLElement>(detailOpen && (wide || !listOpen), timelineScope === 'all' ? 'all' : selection?.kind === 'place' ? `place:${selection.id}` : `country:${selectedCountryCode}`);

  const facetPoints = useMemo(() => completeIndex ? latestMatchingPlaces(points, asOf, { ...DEFAULT_FILTERS, window: 'all' }) : deduplicatePoints(points), [points, completeIndex, asOf]);
  const diseases = useMemo(() => {
    const options = new Map(tally(facetPoints, point => point.disease_id ?? point.disease_label, point => diseaseName(point, locale)).map(option => [option.id, option]));
    for (const [id, disease] of Object.entries(site.diseases)) {
      if (!options.has(id)) options.set(id, { id, label: diseaseName({ disease_id: id, disease_slug: disease.slug, disease_label: disease.name_en, disease_name_en: disease.name_en, disease_name_zh: disease.name_zh }, locale), count: 0 });
    }
    return [...options.values()];
  }, [facetPoints, site.diseases, locale]);
  const countries = useMemo(() => {
    const options = new Map(tally(facetPoints, point => point.country_code, point => countryName(point, locale)).map(option => [option.id, option]));
    for (const [id, country] of Object.entries(site.countries)) {
      if (!options.has(id)) options.set(id, { id, label: countryName({ country_code: id, country_name_en: country.name_en, country_name_zh: country.name_zh }, locale), count: 0 });
    }
    return [...options.values()];
  }, [facetPoints, site.countries, locale]);
  const sources = useMemo(() => tally([...points, ...monitoring], point => point.source_id, point => sourceLabel(point, locale)), [points, monitoring, locale]);
  const hosts = useMemo(() => (['human', 'animal', 'unknown'] as const).map(id => ({
    id, label: HOST_LABELS[id][locale], count: facetPoints.filter(point => (point.host_type ?? 'unknown') === id).length,
  })), [facetPoints, locale]);
  const linked = useMemo(
    () => visible.filter(point => point.host_type === 'human' && point.disease_id && site.series[seriesKey(point.country_code, point.disease_id)]).length,
    [visible, site.series],
  );
  const linksKnown = visible.every(point => !point.disease_id || surveillanceSummaries[String(point.disease_id).toUpperCase()]);

  const outbreakId = selected?.outbreak_id ?? null;
  useEffect(() => {
    if (!outbreakId || requested.current.has(outbreakId)) return;
    requested.current.add(outbreakId);
    setOutbreaks(state => ({ ...state, [outbreakId]: 'loading' }));
    situationData.read<OutbreakFile>(OUTBREAK_URL(outbreakId))
      .then(file => setOutbreaks(state => ({ ...state, [outbreakId]: file })))
      .catch(() => setOutbreaks(state => ({ ...state, [outbreakId]: 'error' })));
  }, [outbreakId, outbreakAttempt]);

  const remote = outbreakId ? outbreaks[outbreakId] : undefined;
  const file = remote && typeof remote === 'object' ? remote : null;
  const outbreakTimeline = useMemo(
    () => deduplicateAlerts(filterPoints(file?.alerts ?? [], filters, asOf), selected?.alert_id),
    [file, filters, asOf, selected?.alert_id],
  );
  const timeline = useMemo(() => selected ? outbreakTimeline.filter(alert => situationPlaceKey(alert) === situationPlaceKey(selected)) : [], [outbreakTimeline, selected]);
  const placesInOutbreak = useMemo(() => {
    const seen = new Map<string, SituationAlertRecord>();
    for (const alert of outbreakTimeline) {
      const key = `${alert.country_code}:${alert.admin_area ?? ''}`;
      if (!seen.has(key)) seen.set(key, alert);
    }
    return [...seen.values()];
  }, [outbreakTimeline]);

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
    return timeline.find(alert => alert.reports.some(report => report.alert_id === selected.alert_id)) ?? timeline.find(samePlace) ?? null;
  }, [timeline, selected]);
  const selectedRecency = selected ? recencyOf(selected.published_date, asOf) : 'stale';
  const headlineIndex = headline ? graph.rows.findIndex(row => row.alert.alert_id === headline.alert_id) : -1;
  const rowLimit = Math.max((outbreakId ? timelineShown[outbreakId] : 0) || TIMELINE_PAGE, headlineIndex + 1);
  const visibleRows = graph.rows.slice(0, rowLimit);

  const applyFilters = (next: Filters) => {
    setFilters(next);
    setTimelineShown({});
    const active = next.disease || next.country || next.source || next.host || next.window !== DEFAULT_FILTERS.window;
    const candidates = completeIndex ? latestMatchingPlaces(points, asOf, next) : visibleSituationPoints(points, asOf, next);
    const target = candidates.find(point => point.alert_id === selectedMarker?.alert_id)
      ?? candidates.find(point => point.outbreak_id === selected?.outbreak_id
        && point.country_code === selected?.country_code && point.admin_area === selected?.admin_area);
    setSelection(!active ? null
      : target ? { kind: 'place', id: target.alert_id }
      : selection?.kind === 'country' && (!next.country || next.country === selection.code) ? selection
      : null);
    setFocusedAlert(null);
    if (!active) setTimelineScope('place');
  };
  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => applyFilters({ ...filters, [key]: value });
  const closeListOnNarrow = () => {
    if (window.matchMedia('(max-width: 899px)').matches) setListMode('closed');
  };
  const selectPlace = (id: string) => {
    setTimelineScope('place');
    setSelection({ kind: 'place', id });
    closeListOnNarrow();
  };
  const selectCountry = (code: string) => {
    setTimelineScope('place');
    setFilters(state => ({ ...state, country: code.toUpperCase() }));
    setTimelineShown({});
    setSelection({ kind: 'country', code: code.toUpperCase() });
    closeListOnNarrow();
  };

  const diseaseOn = Boolean(filters.disease && site.diseases[filters.disease]);
  const regions: RegionTint[] = useMemo(() => {
    if (filters.host === 'animal' || filters.host === 'unknown') return [];
    if (!diseaseOn) {
      return Object.entries(site.countries)
        .filter(([, country]) => country.seriesCount > 0)
        .map(([code, country]) => ({ code, value: country.seriesCount }));
    }
    return Object.entries(site.series).flatMap(([key, glance]) => {
      const [code, diseaseId] = key.split(':');
      return diseaseId === filters.disease ? [{ code, value: glance.value }] : [];
    });
  }, [diseaseOn, filters.disease, filters.host, site.countries, site.series]);
  const notes = useMemo(() => {
    const out: Record<string, string> = {};
    if (filters.host === 'animal' || filters.host === 'unknown') return out;
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
  }, [site.countries, site.series, diseaseOn, filters.disease, filters.host, locale, copy]);

  const detailTitle = timelineScope === 'all' ? copy.allMatchingEvents : selected
    ? diseaseName(selected, locale)
    : selection?.kind === 'country'
      ? countryGlance
        ? countryName({ country_code: selection.code, country_name_en: countryGlance.name_en, country_name_zh: countryGlance.name_zh }, locale)
        : selection.code
      : copy.detailHeading;
  const detailSubtitle = timelineScope === 'all' ? copy.places(visible.length) : selected
    ? placeLabel(selected, countryName(selected, locale))
    : selection?.kind === 'country' && countryGlance
      ? [countryGlance.seriesCount > 0 ? copy.trackedDiseases(countryGlance.seriesCount) : '', countryGlance.latestDate ? formatDay(countryGlance.latestDate, locale) : ''].filter(Boolean).join(' · ')
      : '';

  const windows: Array<[WindowKey, string]> = [['all', copy.winAll], ['7', copy.win7], ['30', copy.win30], ['90', copy.win90]];
  const filtersActive = filters.disease !== '' || filters.country !== '' || filters.source !== '' || Boolean(filters.host) || filters.window !== DEFAULT_FILTERS.window;
  const countryPool = selectedCountryCode
    ? points.filter(point => point.country_code.toUpperCase() === selectedCountryCode
      && (!filters.disease || (point.disease_id ?? point.disease_label) === filters.disease)
      && (!filters.source || point.source_id === filters.source))
    : [];
  const countryAlerts = completeIndex ? latestMatchingPlaces(countryPool, asOf, { ...filters, country: '', disease: '', source: '' }) : deduplicatePoints(sortPoints(
    filterPoints(countryPool, { ...filters, country: '', disease: '', source: '' }, asOf), asOf,
  ));
  const countryHasOlder = filters.window !== 'all' && countryPool.some(point => ageDays(point.published_date, asOf) > Number(filters.window));

  const filterControls = (
    <div className="sit-filter-grid">
      <SearchableFilter label={copy.disease} value={filters.disease} options={diseases} noResults={copy.noFilterResults} anyLabel={copy.any} onChange={value => set('disease', value)} />
      <SearchableFilter label={copy.country} value={filters.country} options={countries} noResults={copy.noFilterResults} anyLabel={copy.any} onChange={value => set('country', value)} />
      <SearchableFilter label={copy.source} value={filters.source} options={sources} noResults={copy.noFilterResults} anyLabel={copy.any} onChange={value => set('source', value)} />
      <SearchableFilter label={HOST_FILTER_LABEL[locale]} value={filters.host || ''} options={hosts} noResults={copy.noFilterResults} anyLabel={copy.any} onChange={value => set('host', value as HostType | '')} />
      <SearchableFilter includeAny={false} noResults={copy.noFilterResults} label={copy.window} value={filters.window} options={windows.map(([id, label]) => ({ id, label, count: -1 }))} anyLabel={copy.winAll} onChange={value => set('window', (value || 'all') as WindowKey)} />
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

      <ActiveSituationFilters locale={locale} filters={filters} options={{ diseases, countries, sources, hosts, windows: windows.map(([id, label]) => ({ id, label, count: -1 })) }} onRemove={key => applyFilters({ ...filters, [key]: DEFAULT_FILTERS[key] })} onReset={() => applyFilters(DEFAULT_FILTERS)} />
      <div className="sit-map-wrap">
        <section className="sit-map" aria-label={copy.mapLabel}>
          {dataState === 'error' ? (
            <div className="sit-note"><p role="status">{copy.dataFailed} <button type="button" className="sit-text-btn focus-ring" onClick={() => setDataAttempt(value => value + 1)}>{copy.retry}</button></p></div>
          ) : dataState !== 'ready' ? (
            <div className="sit-note"><GIDSLoader language={locale} label={copy.mapLoading} /></div>
          ) : Canvas ? (
            <Canvas
              points={visible}
              asOf={asOf}
              selectedId={selectedMarker?.alert_id ?? null}
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

        <aside {...leftScroll} className="sit-panel sit-list" aria-label={copy.filters}>
          <div className="sit-panel-head">
            <h2>{copy.filters}</h2>
            <button type="button" className="sit-reset" disabled={!filtersActive} onClick={() => applyFilters(DEFAULT_FILTERS)}>{copy.reset}</button>
          </div>
          {filterControls}
          {(selected || visible.length > 0 || timelineScope === 'all') && <button type="button" className="sit-text-btn sit-view-timeline" onClick={() => { if (!selection) setTimelineScope('all'); setListMode('closed'); }}>{copy.viewResults} · {copy.places(visible.length)} →</button>}
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
                      <button type="button" className={`sit-item${point.alert_id === selectedMarker?.alert_id ? ' is-selected' : ''}`} aria-pressed={point.alert_id === selectedMarker?.alert_id} onClick={() => selectPlace(point.alert_id)}>
                        <span className={`sit-dot${recency === 'fresh' ? ' is-live' : ''}${recency === 'recent' ? ' is-live-slow' : ''}`} style={{ background: RECENCY_COLORS[recency] }} aria-hidden="true" />
                        <span className="sit-sr">{copy[recency]}</span>
                        <span className="sit-item-main">
                          <strong>{diseaseName(point, locale)}</strong>
                          <span>{placeLabel(point, countryName(point, locale))}</span>
                          <span>{hostLabel(point, locale)}</span>
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
          <MonitoringProducts locale={locale} monitoring={monitoring} filters={filters} asOf={asOf} site={site} points={points} />
        </aside>

        <aside {...rightScroll} className={`sit-panel sit-detail${detailOpen ? ' is-open' : ''}`} aria-live="polite" aria-labelledby="sit-detail-heading">
          <div className="sit-detail-bar">
            {selected && timelineScope === 'place' && (
              <>
                <span className={`sit-dot${selectedRecency === 'fresh' ? ' is-live' : ''}${selectedRecency === 'recent' ? ' is-live-slow' : ''}`} style={{ background: RECENCY_COLORS[selectedRecency] }} aria-hidden="true" />
                <span className="sit-sr">{copy[selectedRecency]}</span>
              </>
            )}
            <div className="sit-detail-title">
              <h2 id="sit-detail-heading">{detailTitle}</h2>
              {detailSubtitle && <p>{detailSubtitle}</p>}
            </div>
            {selected && timelineScope === 'place' && <span className={`sit-status sit-status-${selected.status}`}>{STATUS_LABELS[selected.status][locale]}</span>}
            {detailOpen && <button type="button" className="sit-close" onClick={() => { setSelection(null); setTimelineScope('place'); }} aria-label={copy.close} title={copy.close}>×</button>}
          </div>
          <div className="sit-scope" role="group" aria-label={copy.timelineScope}>
            <button type="button" className="sit-text-btn" data-scope="place" aria-pressed={timelineScope === 'place'} disabled={!selected} onClick={() => setTimelineScope('place')}>{copy.currentPlace}</button>
            <button type="button" className="sit-text-btn" data-scope="all" aria-pressed={timelineScope === 'all'} onClick={() => setTimelineScope('all')}>{copy.allMatchingEvents}</button>
          </div>
          <div hidden={timelineScope !== 'all'}>
            <TimelineOverview locale={locale} alerts={overview.alerts} filters={filters} asOf={asOf} loading={overview.loading} failed={overview.failed} onRetry={overview.retry} onSelect={alert => { setFocusedAlert(alert); selectPlace(alert.alert_id); }} />
          </div>
          <div className="sit-current-place" hidden={timelineScope !== 'place'}>
            {!selection && <p className="sit-muted">{copy.pickHint}</p>}
            {selection?.kind === 'country' && countryGlance && (
              <CountryDossier
                locale={locale}
                country={countryGlance}
                diseaseId={diseaseOn ? filters.disease : ''}
                series={diseaseOn && filters.host !== 'animal' && filters.host !== 'unknown' ? site.series[seriesKey(selection.code, filters.disease)] : undefined}
                seriesWithheldForHost={diseaseOn && (filters.host === 'animal' || filters.host === 'unknown') && Boolean(site.series[seriesKey(selection.code, filters.disease)])}
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
                      <span>{copy.alertsCount(timeline.length)}</span>
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
                {remote === 'error' && <p className="sit-muted" role="status">{copy.timelineFailed} <button type="button" className="sit-text-btn focus-ring" onClick={() => {
                  if (outbreakId) requested.current.delete(outbreakId);
                  setOutbreakAttempt(value => value + 1);
                }}>{copy.retry}</button></p>}
                {file && (
                  <section className="sit-timeline-wrap" aria-labelledby="sit-timeline-heading">
                    <h3 id="sit-timeline-heading">{copy.detailHeading} <small>{copy.alertsCount(timeline.length)}</small></h3>
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
                          selected={row.alert.alert_id === headline?.alert_id}
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
          </div>
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
        </div>
      </div>
    </div>
  );
}
