// World map of the situation stage. Recent reports breathe; older ones stay still.
// The pulse encodes recency, the same fact as the colour, and never severity.
import { useEffect, useMemo, useState } from 'react';
import ReactEChartsCore from '../../lib/echartsReact';
import echarts from '../../lib/echartsMap';
import featureIsoJson from '../../lib/data/world-feature-iso.json' with { type: 'json' };
import type { Locale, MapPoint } from '../../utils/situationTypes.ts';
import { RECENCY_COLORS, buildAnchors, placeLabel, placePoints, recencyOf } from './model.ts';
import { COPY, STATUS_LABELS, countryName, diseaseName, formatDay } from './labels.ts';

const MAP_NAME = 'world-countries-lnglat';
const WORLD_MAP_URL = '/data/world.json';
const DEFAULT_BOUNDS: [[number, number], [number, number]] = [[-180, 84], [180, -58]];
const NAME_TO_ISO = (featureIsoJson as { featureNameToIso: Record<string, string> }).featureNameToIso;
const ISO_TO_NAME: Record<string, string> = {};
for (const [name, code] of Object.entries(NAME_TO_ISO)) {
  if (!ISO_TO_NAME[code]) ISO_TO_NAME[code] = name;
}

export interface RegionTint {
  code: string;
  value: number;
}

export interface CanvasProps {
  points: MapPoint[];
  asOf: string;
  selectedId: string | null;
  highlightCountry: string | null;
  locale: Locale;
  regions: RegionTint[];
  notes: Record<string, string>;
  onSelect: (alertId: string) => void;
  onSelectCountry: (code: string) => void;
}

const escapeHtml = (value: string) =>
  value.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch] as string);

function useTheme(): 'light' | 'dark' {
  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  useEffect(() => {
    const root = document.documentElement;
    const update = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);
  return reduced;
}

function teal(amount: number, theme: 'light' | 'dark'): string {
  const alpha = 0.18 + Math.min(1, Math.max(0, amount)) * 0.62;
  return theme === 'light' ? `rgba(15, 118, 110, ${alpha})` : `rgba(94, 234, 212, ${alpha})`;
}

interface PlacedDatum {
  value: [number, number];
  id: string;
  point: MapPoint;
  symbolSize: number;
  itemStyle: { color: string; borderColor: string; borderWidth: number; opacity: number };
}

export default function SituationMapCanvas({
  points,
  asOf,
  selectedId,
  highlightCountry,
  locale,
  regions,
  notes,
  onSelect,
  onSelectCountry,
}: CanvasProps) {
  const copy = COPY[locale];
  const theme = useTheme();
  const reducedMotion = useReducedMotion();
  const [chart, setChart] = useState<any>(null);
  const [geo, setGeo] = useState<any>(() => echarts.getMap(MAP_NAME)?.geoJson ?? null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (geo) return;
    let live = true;
    fetch(WORLD_MAP_URL)
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then(json => {
        if (!live) return;
        if (!echarts.getMap(MAP_NAME)) echarts.registerMap(MAP_NAME, json);
        setGeo(json);
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [geo]);

  useEffect(() => {
    if (!chart) return;
    const node = chart.getDom?.() as HTMLElement | undefined;
    const target = node?.parentElement ?? node;
    if (!target) return;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(target);
    return () => observer.disconnect();
  }, [chart]);

  const anchors = useMemo(() => buildAnchors(geo, NAME_TO_ISO), [geo]);
  const { placed, unplaced } = useMemo(() => placePoints(points, anchors), [points, anchors]);

  const palette =
    theme === 'light'
      ? { land: '#e7eef4', border: '#b6c8d8', bg: '#d5e0ea', tipBg: '#ffffff', tipText: '#17304d', ring: '#17304d', hover: '#d5ebe7', focus: '#0f766e' }
      : { land: '#1b2a3c', border: '#102033', bg: '#121c2a', tipBg: '#162334', tipText: '#e2e8f0', ring: '#ffffff', hover: '#1c3d45', focus: '#5eead4' };

  const highlighted = highlightCountry?.toUpperCase() ?? '';
  const maxTint = regions.reduce((max, region) => Math.max(max, region.value), 0);

  const option = useMemo(() => {
    if (!geo) return {};
    const fresh: PlacedDatum[] = [];
    const recent: PlacedDatum[] = [];
    const still: PlacedDatum[] = [];
    for (const { point, lng, lat } of placed) {
      const recency = recencyOf(point.published_date, asOf);
      const selected = point.alert_id === selectedId;
      const inCountry = point.country_code.toUpperCase() === highlighted;
      const datum: PlacedDatum = {
        value: [lng, lat],
        id: point.alert_id,
        point,
        symbolSize: selected ? 16 : recency === 'fresh' ? 12 : recency === 'recent' ? 10 : 8,
        itemStyle: {
          color: RECENCY_COLORS[recency],
          borderColor: selected ? palette.ring : 'rgba(255,255,255,0.8)',
          borderWidth: selected ? 2.5 : 1,
          opacity: selected || inCountry || !highlighted ? 0.96 : 0.45,
        },
      };
      const breathe = !reducedMotion && (selected || recency === 'fresh' || recency === 'recent');
      if (breathe && (selected || recency === 'fresh')) fresh.push(datum);
      else if (breathe && recency === 'recent') recent.push(datum);
      else still.push(datum);
    }

    const effect = (data: PlacedDatum[], period: number, scale: number) => ({
      type: 'effectScatter',
      coordinateSystem: 'geo',
      geoIndex: 0,
      z: 4,
      silent: false,
      showEffectOn: 'render',
      rippleEffect: { brushType: 'stroke', scale, period, number: 2 },
      data,
    });

    const geoRegions = regions.flatMap(region => {
      const name = ISO_TO_NAME[region.code.toUpperCase()];
      if (!name) return [];
      const weight = maxTint > 0 ? Math.log1p(region.value) / Math.log1p(maxTint) : 0;
      const selected = region.code.toUpperCase() === highlighted;
      return [{
        name,
        itemStyle: {
          areaColor: teal(weight, theme),
          borderColor: selected ? palette.focus : palette.border,
          borderWidth: selected ? 1.8 : 0.45,
        },
      }];
    });

    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        confine: true,
        backgroundColor: palette.tipBg,
        textStyle: { color: palette.tipText, fontSize: 12 },
        formatter: (params: any) => {
          const point: MapPoint | undefined = params?.data?.point;
          if (point) {
            const place = escapeHtml(placeLabel(point, countryName(point, locale)));
            return `<strong>${escapeHtml(diseaseName(point, locale))}</strong><br/>${place}<br/>${escapeHtml(
              STATUS_LABELS[point.status][locale],
            )} · ${escapeHtml(formatDay(point.published_date, locale))}`;
          }
          const code = params?.name ? NAME_TO_ISO[String(params.name)] : '';
          const note = code ? notes[code.toUpperCase()] : '';
          return note ? escapeHtml(note) : escapeHtml(String(params?.name ?? ''));
        },
      },
      geo: {
        map: MAP_NAME,
        roam: true,
        boundingCoords: DEFAULT_BOUNDS,
        left: 0,
        right: 0,
        top: 0,
        bottom: 0,
        scaleLimit: { min: 0.7, max: 12 },
        silent: false,
        selectedMode: false,
        itemStyle: { areaColor: palette.land, borderColor: palette.border, borderWidth: 0.45 },
        emphasis: { label: { show: false }, itemStyle: { areaColor: palette.hover } },
        label: { show: false },
        regions: geoRegions,
      },
      series: [
        fresh.length ? effect(fresh, 2.6, 3.2) : null,
        recent.length ? effect(recent, 4.8, 2.2) : null,
        still.length
          ? { type: 'scatter', coordinateSystem: 'geo', geoIndex: 0, z: 3, data: still }
          : null,
      ].filter(Boolean),
    };
  }, [geo, placed, asOf, selectedId, highlighted, locale, palette, regions, notes, reducedMotion, maxTint, theme]);

  const events = useMemo(
    () => ({
      click: (event: any) => {
        const id = event?.data?.id;
        if (typeof id === 'string') {
          onSelect(id);
          return;
        }
        const code = event?.name ? NAME_TO_ISO[String(event.name)] : '';
        if (code) onSelectCountry(code.toUpperCase());
      },
    }),
    [onSelect, onSelectCountry],
  );

  const zoom = (factor: number) => chart?.dispatchAction({ type: 'geoRoam', componentType: 'geo', geoIndex: 0, zoom: factor });
  const reset = () => chart?.setOption({ geo: { zoom: 1, boundingCoords: DEFAULT_BOUNDS } });

  if (failed) return <p className="sit-note" role="status">{copy.mapFailed}</p>;
  if (!geo) return <p className="sit-note" role="status">{copy.mapLoading}</p>;

  return (
    <div className="sit-canvas" style={{ background: palette.bg }} role="img" aria-label={copy.mapLabel}>
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height: '100%', width: '100%' }}
        onEvents={events}
        onChartReady={(instance: any) => setChart(instance)}
      />
      <div className="sit-zoom" role="group" aria-label={copy.mapLabel}>
        <button type="button" onClick={() => zoom(1.25)} aria-label="+">+</button>
        <button type="button" onClick={() => zoom(0.8)} aria-label="−">−</button>
        <button type="button" onClick={reset} aria-label="↺">↺</button>
      </div>
      {unplaced.length > 0 && <span className="sit-unplaced">{copy.unplaced}: {unplaced.length}</span>}
    </div>
  );
}
