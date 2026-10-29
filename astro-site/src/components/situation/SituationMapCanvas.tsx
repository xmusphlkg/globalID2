// World map layer of the Situation explorer. Loaded after the page is interactive so the
// list, filters and timeline work without it (and without the echarts bundle).
import { useEffect, useMemo, useState } from 'react';
import ReactEChartsCore from '../../lib/echartsReact';
import echarts from '../../lib/echartsMap';
import featureIsoJson from '../../lib/data/world-feature-iso.json' with { type: 'json' };
import type { Locale, MapPoint } from '../../utils/situationTypes.ts';
import { RECENCY_COLORS, buildAnchors, placeLabel, placePoints, recencyOf } from './model.ts';
import { COPY, STATUS_LABELS, countryName, diseaseName, formatDay } from './labels.ts';

const MAP_NAME = 'world-countries-lnglat';
const WORLD_MAP_URL = '/data/world.json';
const DEFAULT_ZOOM = 1.45;
const DEFAULT_CENTER: [number, number] = [12, 18];
const NAME_TO_ISO = (featureIsoJson as { featureNameToIso: Record<string, string> }).featureNameToIso;

export interface CanvasProps {
  points: MapPoint[];
  asOf: string;
  selectedId: string | null;
  locale: Locale;
  height?: number;
  onSelect: (alertId: string) => void;
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

export default function SituationMapCanvas({ points, asOf, selectedId, locale, height = 440, onSelect }: CanvasProps) {
  const copy = COPY[locale];
  const theme = useTheme();
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

  const anchors = useMemo(() => buildAnchors(geo, NAME_TO_ISO), [geo]);
  const { placed, unplaced } = useMemo(() => placePoints(points, anchors), [points, anchors]);

  const palette =
    theme === 'light'
      ? { land: '#fbfdff', border: '#b6c8d8', bg: 'linear-gradient(180deg, #eef4fa 0%, #dde8f2 100%)', tipBg: '#ffffff', tipText: '#17304d', ring: '#17304d' }
      : { land: '#1b2a3c', border: '#102033', bg: 'linear-gradient(180deg, #152233 0%, #111b28 100%)', tipBg: '#162334', tipText: '#e2e8f0', ring: '#ffffff' };

  const option = useMemo(
    () =>
      geo
        ? {
            backgroundColor: 'transparent',
            tooltip: {
              trigger: 'item',
              confine: true,
              backgroundColor: palette.tipBg,
              textStyle: { color: palette.tipText, fontSize: 12 },
              formatter: (params: any) => {
                const point: MapPoint | undefined = params?.data?.point;
                if (!point) return '';
                const place = escapeHtml(placeLabel(point, countryName(point, locale)));
                return `<strong>${escapeHtml(diseaseName(point, locale))}</strong><br/>${place}<br/>${escapeHtml(
                  STATUS_LABELS[point.status][locale],
                )} · ${escapeHtml(formatDay(point.published_date, locale))}`;
              },
            },
            geo: {
              map: MAP_NAME,
              roam: true,
              scaleLimit: { min: 0.7, max: 12 },
              silent: true,
              itemStyle: { areaColor: palette.land, borderColor: palette.border, borderWidth: 0.5 },
            },
            series: [
              {
                type: 'scatter',
                coordinateSystem: 'geo',
                z: 3,
                data: placed.map(({ point, lng, lat }) => {
                  const recency = recencyOf(point.published_date, asOf);
                  const selected = point.alert_id === selectedId;
                  return {
                    value: [lng, lat],
                    id: point.alert_id,
                    point,
                    symbolSize: selected ? 17 : recency === 'fresh' ? 13 : 10,
                    itemStyle: {
                      color: RECENCY_COLORS[recency],
                      borderColor: selected ? palette.ring : 'rgba(255,255,255,0.75)',
                      borderWidth: selected ? 3 : 1,
                      opacity: 0.95,
                    },
                  };
                }),
              },
            ],
          }
        : {},
    [geo, placed, asOf, selectedId, locale, palette.land, palette.border, palette.tipBg, palette.tipText, palette.ring],
  );

  const events = useMemo(
    () => ({
      click: (event: any) => {
        const id = event?.data?.id;
        if (typeof id === 'string') onSelect(id);
      },
    }),
    [onSelect],
  );

  const zoom = (factor: number) => chart?.dispatchAction({ type: 'geoRoam', geoIndex: 0, zoom: factor });
  const reset = () => chart?.setOption({ geo: { zoom: DEFAULT_ZOOM, center: DEFAULT_CENTER } });

  if (failed) return <p className="sit-note" role="status">{copy.mapFailed}</p>;
  if (!geo) return <p className="sit-note" role="status">{copy.mapLoading}</p>;

  return (
    <div className="sit-canvas" style={{ height, background: palette.bg }} role="img" aria-label={copy.mapLabel}>
      <ReactEChartsCore
        echarts={echarts}
        option={option}
        style={{ height: '100%', width: '100%' }}
        onEvents={events}
        onChartReady={(instance: any) => {
          setChart(instance);
          instance.setOption({ geo: { zoom: DEFAULT_ZOOM, center: DEFAULT_CENTER } });
        }}
      />
      <div className="sit-zoom" role="group">
        <button type="button" onClick={() => zoom(1.25)} aria-label="+">+</button>
        <button type="button" onClick={() => zoom(0.8)} aria-label="−">−</button>
        <button type="button" onClick={reset} aria-label="↺">↺</button>
      </div>
      {unplaced.length > 0 && <span className="sit-unplaced">{copy.unplaced}: {unplaced.length}</span>}
    </div>
  );
}
