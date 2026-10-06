// Interactive coverage map. Country dots stay quiet and compact; province dots
// pulse only when a sub-national feed is available. Coordinates for countries
// are derived from the bundled world GeoJSON and the MIT-licensed flag-icons
// country catalogue, so the map does not depend on a third-party tile service.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactEChartsCore from '../../lib/echartsReact';
import echarts from '../../lib/echartsMap';
import countryCatalogue from 'flag-icons/country.json' with { type: 'json' };
import { getFlagAssetPath } from '../../lib/country-flag';
import {
  COUNTRY_COVERAGE,
  getCoverageDisplayName,
  hasCountryDataSnapshot,
  resolveCoverageStatus,
  type CoverageStatus,
} from '../../lib/country-coverage';

const MAP_NAME = 'world-countries-lnglat';
const LOCAL_WORLD_MAP_URL = '/data/world.json';

type MarkerStatus = CoverageStatus | 'Unsupported';
type MarkerKind = 'country' | 'subdivision';

interface MetaCountry {
  code: string;
  name: string;
  name_en?: string;
  name_zh?: string;
  parent_code?: string | null;
  location_type?: string;
  total_cases?: number;
  total_deaths?: number;
  disease_count?: number;
  data_available?: boolean;
  record_count?: number;
  date_range?: { start?: string | null; end?: string | null } | null;
}

interface Marker {
  iso2: string;
  name: string;
  lat: number;
  lng: number;
  status: MarkerStatus;
  kind: MarkerKind;
  statusLabel: string;
  href?: string;
  meta?: MetaCountry;
}

interface DotPos extends Marker {
  px: number;
  py: number;
}

interface Props {
  metaCountries?: MetaCountry[];
  height?: number;
  initialLanguage?: 'en' | 'zh' | 'fr';
}

type CountryFilterWindow = Window & { __globalIdCountryFilterCodes?: string[] };
type CatalogueCountry = { code?: string; name?: string; iso?: boolean };

function getGeoJsonBounds(geoJson: any) {
  const bounds = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
  const visit = (value: any) => {
    if (Array.isArray(value) && typeof value[0] === 'number' && typeof value[1] === 'number') {
      bounds.minX = Math.min(bounds.minX, value[0]); bounds.maxX = Math.max(bounds.maxX, value[0]);
      bounds.minY = Math.min(bounds.minY, value[1]); bounds.maxY = Math.max(bounds.maxY, value[1]);
      return;
    }
    if (Array.isArray(value)) value.forEach(visit);
  };
  geoJson?.features?.forEach((feature: any) => visit(feature.geometry?.coordinates));
  return bounds;
}

function isLngLatWorldGeoJson(geoJson: any) {
  const { minX, maxX, minY, maxY } = getGeoJsonBounds(geoJson);
  return Number.isFinite(minX) && minX >= -180.5 && maxX <= 180.5 && minY >= -90.5 && maxY <= 90.5;
}

async function fetchLngLatWorldGeoJson() {
  const response = await fetch(LOCAL_WORLD_MAP_URL);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const geoJson = await response.json();
  if (!isLngLatWorldGeoJson(geoJson)) throw new Error('World map is not lng/lat GeoJSON');
  return geoJson;
}

function centroid(feature: any): [number, number] | null {
  const points: Array<[number, number]> = [];
  const visit = (value: any) => {
    if (Array.isArray(value) && typeof value[0] === 'number' && typeof value[1] === 'number') {
      points.push([value[0], value[1]]); return;
    }
    if (Array.isArray(value)) value.forEach(visit);
  };
  visit(feature?.geometry?.coordinates);
  if (!points.length) return null;
  return [points.reduce((sum, point) => sum + point[0], 0) / points.length, points.reduce((sum, point) => sum + point[1], 0) / points.length];
}

function normaliseName(value: string) {
  return String(value || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/&/g, 'and').replace(/[^a-z0-9]+/g, ' ').trim().replace(/^(the|republic of|state of)\s+/, '');
}

const NAME_ALIASES: Record<string, string[]> = {
  'czechia': ['czech republic'], 'eswatini': ['swaziland'], 'north macedonia': ['macedonia'],
  'myanmar': ['burma'], 'timor leste': ['east timor'], 'brunei': ['brunei darussalam'],
  'laos': ['lao pdr', 'lao people s democratic republic'], 'russia': ['russian federation'],
  'south korea': ['korea'], 'moldova': ['republic of moldova'], 'vietnam': ['viet nam'],
  'tanzania': ['united republic of tanzania'], 'bolivia': ['bolivia plurinational state of'],
  'venezuela': ['venezuela bolivarian republic of'], 'iran': ['iran islamic republic of'],
  'syria': ['syrian arab republic'], 'democratic republic of the congo': ['dem rep congo', 'democratic republic of congo'],
  'congo': ['republic of the congo'], 'palestine': ['palestine west bank and gaza'],
  'united states': ['united states of america'], 'united kingdom': ['uk'],
};

// Open-source map data sometimes omits tiny islands or uses a disputed label.
// These fallbacks keep the marker layer useful while remaining auditable.
const FALLBACK_POINTS: Record<string, [number, number]> = {
  AD: [1.58, 42.55], MC: [7.42, 43.73], SM: [12.46, 43.94], VA: [12.45, 41.9],
  LI: [9.55, 47.14], SG: [103.82, 1.35], HK: [114.17, 22.32], MO: [113.55, 22.2],
  TW: [121, 23.7], MV: [73.22, 4.2], MT: [14.4, 35.9],
};

const SUBDIVISION_POINTS: Record<string, [number, number]> = {
  'AU-ACT': [149.13, -35.28], 'AU-NSW': [147, -32], 'AU-NT': [133, -19.5], 'AU-QLD': [145, -22],
  'AU-SA': [136, -30], 'AU-TAS': [147, -42], 'AU-VIC': [144, -36.8], 'AU-WA': [121, -25],
  'CA-ON': [-85, 50], 'CN-AH': [117.2, 31.8], 'CN-BJ': [116.4, 39.9], 'CN-CQ': [107.9, 29.6],
  'CN-FJ': [118.3, 26.1], 'CN-GD': [113.3, 23.1], 'CN-GS': [100.2, 38.4], 'CN-GX': [108.8, 23.8],
  'CN-GZ': [106.7, 26.8], 'CN-HA': [113.6, 34.8], 'CN-HB': [112.3, 30.9], 'CN-HE': [114.5, 38],
  'CN-HI': [109.7, 19.2], 'CN-HL': [127.8, 47], 'CN-HN': [111.7, 27.6], 'CN-JL': [126.2, 43.7],
  'CN-JS': [119.4, 32.9], 'CN-JX': [115.7, 27.6], 'CN-LN': [122.6, 41.3], 'CN-NM': [111.7, 44],
  'CN-NX': [105.2, 37.2], 'CN-QH': [96, 35.2], 'CN-SC': [102.7, 30.7], 'CN-SD': [118.1, 36.3],
  'CN-SH': [121.5, 31.2], 'CN-SN': [108.9, 35.2], 'CN-SX': [112.3, 37.8], 'CN-TJ': [117.4, 39.3],
  'CN-XJ': [85.5, 41.1], 'CN-XZ': [88.4, 31.7], 'CN-YN': [101.5, 25.5], 'CN-ZJ': [120.2, 29.2],
};

function makeCountryPoints(worldGeoJson: any, metaByCode: Record<string, MetaCountry>, lang: 'en' | 'zh' | 'fr'): Marker[] {
  const featureByName = new Map<string, any>();
  for (const feature of worldGeoJson?.features ?? []) {
    const name = normaliseName(feature?.properties?.name);
    if (name) featureByName.set(name, feature);
  }
  const coverageByCode = new Map(COUNTRY_COVERAGE.filter(c => !c.code.includes('-')).map(c => [c.code, c]));
  const catalogue = (countryCatalogue as CatalogueCountry[]).filter(country => country.iso && /^[a-z]{2}$/i.test(country.code || ''));
  const markers: Marker[] = [];
  for (const country of catalogue) {
    const code = String(country.code).toUpperCase();
    const coverage = coverageByCode.get(code);
    const meta = metaByCode[code];
    const names = [country.name || '', ...(NAME_ALIASES[normaliseName(country.name || '')] || [])];
    const feature = names.map(normaliseName).map(name => featureByName.get(name)).find(Boolean);
    const point = coverage ? [coverage.lng, coverage.lat] as [number, number] : centroid(feature) || FALLBACK_POINTS[code];
    if (!point) continue;
    const hasData = hasCountryDataSnapshot(meta);
    const status: MarkerStatus = coverage
      ? resolveCoverageStatus(coverage, hasData)
      : hasData ? 'Supported' : 'Unsupported';
    const name = coverage
      ? getCoverageDisplayName(coverage, lang, lang === 'zh' ? (meta?.name_zh || meta?.name) : (meta?.name_en || meta?.name))
      : (lang === 'zh' ? (meta?.name_zh || meta?.name || country.name || code) : (meta?.name_en || meta?.name || country.name || code));
    markers.push({ iso2: code, name, lat: point[1], lng: point[0], status, kind: 'country', statusLabel: status === 'Supported' ? 'Supported' : status === 'Scheduled' ? 'Planned' : 'Not supported', href: hasData ? `/countries/${code.toLowerCase()}/` : undefined, meta });
  }
  // A few boundary features (small islands and disputed territories) have no
  // ISO entry in the catalogue. Keep them visible as unsupported hollow dots
  // instead of silently dropping countries from the baseline layer.
  const representedNames = new Set(markers.map(marker => normaliseName(marker.name)));
  let fallbackIndex = 0;
  for (const feature of worldGeoJson?.features ?? []) {
    const featureName = String(feature?.properties?.name || '').trim();
    const key = normaliseName(featureName);
    const point = centroid(feature);
    if (!key || !point || representedNames.has(key)) continue;
    representedNames.add(key);
    markers.push({ iso2: `ZZ-${fallbackIndex++}`, name: featureName, lat: point[1], lng: point[0], status: 'Unsupported', kind: 'country', statusLabel: 'Not supported' });
  }
  return markers;
}

export default function CountriesMapView({ metaCountries = [], height = 450, initialLanguage = 'en' }: Props) {
  const chartRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  const [mapReady, setMapReady] = useState(false);
  const [worldGeoJson, setWorldGeoJson] = useState<any>(null);
  const [dotPositions, setDotPositions] = useState<DotPos[]>([]);
  const [hovered, setHovered] = useState<DotPos | null>(null);
  const [visibleCountryCodes, setVisibleCountryCodes] = useState<string[] | null>(() => {
    if (typeof window === 'undefined') return null;
    return (window as CountryFilterWindow).__globalIdCountryFilterCodes ?? null;
  });
  const metaByCode = useMemo(() => Object.fromEntries(metaCountries.map(meta => [meta.code.toUpperCase(), meta])), [metaCountries]);
  const allMarkers = useMemo(() => {
    if (!worldGeoJson) return [];
    const countries = makeCountryPoints(worldGeoJson, metaByCode, initialLanguage);
    const subdivisions: Marker[] = metaCountries
      .filter(meta => meta.parent_code || meta.location_type === 'subdivision')
      .map(meta => {
        const code = meta.code.toUpperCase();
        const point = SUBDIVISION_POINTS[code];
        if (!point) return null;
        const hasData = hasCountryDataSnapshot(meta);
        const status: MarkerStatus = hasData ? 'Supported' : (COUNTRY_COVERAGE.find(item => item.code === code)?.status || 'Scheduled');
        return { iso2: code, name: initialLanguage === 'zh' ? (meta.name_zh || meta.name_en || meta.name) : (meta.name_en || meta.name), lat: point[1], lng: point[0], status, kind: 'subdivision', statusLabel: status === 'Supported' ? 'Province supported' : status === 'Scheduled' ? 'Province planned' : 'Not supported', href: hasData ? `/countries/${code.toLowerCase()}/` : undefined, meta } as Marker;
      }).filter((marker): marker is Marker => Boolean(marker));
    return [...countries, ...subdivisions];
  }, [worldGeoJson, metaByCode, metaCountries, initialLanguage]);
  const displayedMarkers = useMemo(() => {
    if (visibleCountryCodes === null) return allMarkers;
    const visible = new Set(visibleCountryCodes.map(code => code.toUpperCase()));
    return allMarkers.filter(marker => visible.has(marker.iso2) || visible.has(marker.iso2.split('-')[0]));
  }, [allMarkers, visibleCountryCodes]);

  useEffect(() => {
    const handle = (event: Event) => setVisibleCountryCodes((event as CustomEvent<{ codes?: string[] }>).detail?.codes ?? null);
    window.addEventListener('globalid:country-filter', handle);
    return () => window.removeEventListener('globalid:country-filter', handle);
  }, []);
  useEffect(() => {
    const root = document.documentElement;
    const update = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    update();
    const observer = new MutationObserver(update); observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (echarts.getMap(MAP_NAME)) { setMapReady(true); return; }
    let cancelled = false;
    fetchLngLatWorldGeoJson().then(geoJson => {
      if (cancelled) return;
      echarts.registerMap(MAP_NAME, geoJson); setWorldGeoJson(geoJson); setMapReady(true);
    }).catch(error => console.error('Unable to load world map', error));
    return () => { cancelled = true; };
  }, []);
  useEffect(() => {
    if (mapReady && !worldGeoJson) setWorldGeoJson(echarts.getMap(MAP_NAME)?.geoJson);
  }, [mapReady, worldGeoJson]);

  const computePositions = useCallback(() => {
    const instance = chartRef.current?.getEchartsInstance?.();
    if (!instance) return;
    const rect = containerRef.current?.getBoundingClientRect();
    const next: DotPos[] = [];
    for (const marker of displayedMarkers) {
      const point = instance.convertToPixel({ geoIndex: 0 }, [marker.lng, marker.lat]) as [number, number] | null;
      if (point) next.push({ ...marker, px: point[0], py: point[1] });
    }
    setDotPositions(next);
    if (hovered && !next.some(marker => marker.iso2 === hovered.iso2)) setHovered(null);
    void rect;
  }, [displayedMarkers, hovered]);
  useEffect(() => {
    if (!mapReady) return;
    const timers = [setTimeout(computePositions, 250), setTimeout(computePositions, 800)];
    return () => timers.forEach(clearTimeout);
  }, [mapReady, computePositions]);
  useEffect(() => {
    const element = containerRef.current; if (!element) return;
    const observer = new ResizeObserver(() => setTimeout(computePositions, 60)); observer.observe(element);
    return () => observer.disconnect();
  }, [computePositions]);

  const palette = theme === 'light' ? {
    mapBg: 'linear-gradient(180deg, #eef4fa 0%, #dde8f2 100%)', areaColor: '#fbfdff', areaBorder: '#b6c8d8', areaHover: '#e4edf5',
    supported: '#138a70', supportedBorder: '#0d6d8c', planned: '#3aa66f', unsupported: '#8497aa', tooltipBg: '#fff', tooltipBorder: '#c7d7e6', text: '#17304d', muted: '#657b92',
  } : {
    mapBg: 'linear-gradient(180deg, #152233 0%, #111b28 100%)', areaColor: '#1b2a3c', areaBorder: '#102033', areaHover: '#25374d',
    supported: '#14b8a6', supportedBorder: '#0d9488', planned: '#4ade80', unsupported: '#8091a5', tooltipBg: '#162334', tooltipBorder: '#304156', text: '#e2e8f0', muted: '#94a3b8',
  };
  const option = useMemo(() => mapReady ? ({
    backgroundColor: 'transparent',
    geo: { map: MAP_NAME, roam: true, scaleLimit: { min: 0.7, max: 8 }, silent: true, itemStyle: { areaColor: palette.areaColor, borderColor: palette.areaBorder, borderWidth: 0.5 }, emphasis: { itemStyle: { areaColor: palette.areaHover }, label: { show: false } } },
    // The interactive HTML dots below are the single source of marker pixels.
    // Keeping an invisible scatter series gives ECharts a geo coordinate layer
    // to roam without doubling each marker visually.
    series: [{ type: 'scatter', coordinateSystem: 'geo', silent: true, data: displayedMarkers.map(marker => ({ value: [marker.lng, marker.lat], name: marker.name })), symbolSize: 0 }],
  }) : {}, [mapReady, displayedMarkers, palette]);
  const handleEvents = useMemo(() => ({ finished: () => setTimeout(computePositions, 80), georoam: () => setTimeout(computePositions, 30) }), [computePositions]);
  const zoom = (factor: number) => chartRef.current?.getEchartsInstance?.()?.dispatchAction({ type: 'geoRoam', geoIndex: 0, zoom: factor });
  const resetZoom = () => chartRef.current?.getEchartsInstance?.()?.setOption({ geo: { zoom: 1, center: [0, 0] } });

  if (!mapReady) return <div style={{ height }} className="flex items-center justify-center text-sm text-[rgb(var(--text-muted))]">{initialLanguage === 'zh' ? '正在加载地图…' : 'Loading map…'}</div>;
  return (
    <div ref={containerRef} className="coverage-map-canvas" style={{ position: 'relative', height, overflow: 'hidden', background: palette.mapBg }}>
      <ReactEChartsCore ref={chartRef} echarts={echarts} option={option} style={{ height: '100%', width: '100%' }} onEvents={handleEvents} notMerge />
      <div className="coverage-map-controls" role="group" aria-label={initialLanguage === 'zh' ? '地图缩放' : 'Map zoom'}>
        <button type="button" onClick={() => zoom(1.25)} aria-label={initialLanguage === 'zh' ? '放大地图' : 'Zoom in'}>+</button>
        <button type="button" onClick={() => zoom(0.8)} aria-label={initialLanguage === 'zh' ? '缩小地图' : 'Zoom out'}>−</button>
        <button type="button" onClick={resetZoom} aria-label={initialLanguage === 'zh' ? '重置地图缩放' : 'Reset map zoom'}>↺</button>
      </div>
      <span className="coverage-map-hint">{initialLanguage === 'zh' ? '滚轮缩放 · 拖拽平移' : 'Scroll to zoom · drag to pan'}</span>
      {dotPositions.map(marker => {
        const isSubdivision = marker.kind === 'subdivision';
        const isUnsupported = marker.status === 'Unsupported';
        const isPlanned = marker.status === 'Scheduled';
        const color = isUnsupported ? palette.unsupported : isPlanned ? palette.planned : palette.supported;
        return <button key={marker.iso2} type="button" className={`coverage-map-dot ${isSubdivision ? 'is-subdivision' : 'is-country'} ${isUnsupported ? 'is-unsupported' : ''} ${isPlanned ? 'is-planned' : ''}`} style={{ left: marker.px, top: marker.py, '--dot-color': color } as React.CSSProperties} aria-label={`${marker.name} — ${marker.statusLabel}`} onMouseEnter={() => setHovered(marker)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(marker)} onBlur={() => setHovered(null)} onClick={() => marker.href && (window.location.href = `${initialLanguage === 'zh' ? '/zh' : initialLanguage === 'fr' ? '/fr' : ''}${marker.href}`)} />;
      })}
      {hovered && <div className="coverage-map-tooltip" style={{ left: Math.min(Math.max(8, hovered.px + 12), Math.max(8, (containerRef.current?.clientWidth || 800) - 190)), top: Math.min(Math.max(8, hovered.py - 46), Math.max(8, (containerRef.current?.clientHeight || height) - 82)), background: palette.tooltipBg, borderColor: palette.tooltipBorder, color: palette.text }} role="status">
        <div className="coverage-map-tooltip-title"><img src={getFlagAssetPath(hovered.iso2)} alt="" aria-hidden="true" /> <strong>{hovered.name}</strong></div>
        <div className="coverage-map-tooltip-meta">{hovered.kind === 'subdivision' ? (initialLanguage === 'zh' ? '省级/地区' : 'Province / region') : (initialLanguage === 'zh' ? '国家' : 'Country')} · {hovered.statusLabel}</div>
        {hovered.href && <div className="coverage-map-tooltip-action">{initialLanguage === 'zh' ? '单击打开详情页 ↗' : 'Click to open details ↗'}</div>}
      </div>}
    </div>
  );
}
