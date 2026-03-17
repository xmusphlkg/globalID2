// src/components/charts/CountriesMapView.tsx
// Flat world map with SVG leader lines (折线) connecting each country dot to an info box.
// Countries + status sourced from support_map.js data.

import React, {
  useRef, useState, useEffect, useCallback, useMemo,
} from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';

const MAP_NAME = 'world';
const BOX_W = 126;
const BOX_H = 46;

interface CountryDef {
  iso2: string;
  name: string;
  lat: number;
  lng: number;
}

const ALL_COUNTRIES: CountryDef[] = [
  { iso2: 'CN', name: 'China',          lat: 35,    lng: 104 },
  { iso2: 'TH', name: 'Thailand',       lat: 15.5,  lng: 100.5 },
  { iso2: 'AU', name: 'Australia',      lat: -25,   lng: 133 },
  { iso2: 'US', name: 'United States',  lat: 39,    lng: -98 },
  { iso2: 'GB', name: 'United Kingdom', lat: 54,    lng: -3 },
  { iso2: 'KR', name: 'South Korea',    lat: 36.5,  lng: 127.5 },
  { iso2: 'NZ', name: 'New Zealand',    lat: -41,   lng: 171 },
  { iso2: 'SE', name: 'Sweden',         lat: 62,    lng: 18 },
  { iso2: 'JP', name: 'Japan',          lat: 36,    lng: 138 },
  { iso2: 'SG', name: 'Singapore',      lat: 1.35,  lng: 103.8 },
];

// Box offset (dx, dy) from dot centre → box centre, tuned for a ~900 px wide chart.
// Positive dx → box to the right; negative dx → box to the left.
// Spread out to avoid overlap in the crowded East-Asia cluster, keeping boxes looking neat.
const BASE_OFFSETS: Record<string, [number, number]> = {
  // East Asia cluster — spread in all four directions
  CN: [  -60,  0],   // Top-Right / Top
  TH: [ -60,   0],   // Left, slightly down
  KR: [  -60,  -95],   // Right, very far up
  JP: [ 95, 0],   // Right, max up
  SG: [-60,   60],   // Left, far down
  // Oceania
  AU: [  80,  -40],   // Right, down
  NZ: [ 80,   0],   // Left
  // Europe
  GB: [-110,  -40],   // Far left
  SE: [-110,  -85],   // Far upper-left
  // Americas
  US: [-120,  -35],   // Far left
};

function fmtNum(n: number) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(0) + 'K';
  return n.toLocaleString();
}

// ─── Types ────────────────────────────────────────────────────────────────────

interface MetaCountry {
  code: string;
  name: string;
  total_cases: number;
  total_deaths: number;
  disease_count: number;
}

interface DotPos {
  iso2: string;
  status: 'Supported' | 'Scheduled';
  name: string;
  px: number; py: number;   // dot pixel position (chart-relative)
  bx: number; by: number;   // box centre pixel position
  meta?: MetaCountry;
}

interface Props {
  metaCountries?: MetaCountry[];
  height?: number;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function CountriesMapView({ metaCountries = [], height = 450 }: Props) {
  const chartRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  const [mapReady, setMapReady] = useState(false);
  const [dotPositions, setDotPositions] = useState<DotPos[]>([]);

  // Build meta lookup
  const metaByCode = useMemo(
    () => Object.fromEntries(metaCountries.map(m => [m.code.toUpperCase(), m])),
    [metaCountries],
  );

  const countriesWithStatus = useMemo(
    () => ALL_COUNTRIES.map((c) => {
      const meta = metaByCode[c.iso2];
      return {
        ...c,
        name: meta?.name ?? c.name,
        status: (meta ? 'Supported' : 'Scheduled') as 'Supported' | 'Scheduled',
      };
    }),
    [metaByCode],
  );

  // Theme detection ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const update = () =>
      setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);

  // Load world GeoJSON ───────────────────────────────────────────────────────
  useEffect(() => {
    if (echarts.getMap(MAP_NAME)) { setMapReady(true); return; }
    // Fetch an unprojected, standard lat/lng world map from a reliable CDN
    fetch('https://registry.npmmirror.com/echarts/4.9.0/files/map/json/world.json')
      .then(r => r.json())
      .then(gj => { echarts.registerMap(MAP_NAME, gj); setMapReady(true); })
      .catch(() => {
        // Fallback or use local if CDN fails
        fetch('/data/world.json')
          .then(r => r.json())
          .then(gj => { echarts.registerMap(MAP_NAME, gj); setMapReady(true); });
      });
  }, []);

  // Convert geo → pixel and place boxes ─────────────────────────────────────
  const computePositions = useCallback(() => {
    const inst = chartRef.current?.getEchartsInstance?.();
    if (!inst) return;

    const rect = containerRef.current?.getBoundingClientRect();
    const cw = rect?.width ?? 900;
    const ch = rect?.height ?? 450;
    const scale = Math.max(0.5, cw / 900);

    const hw = BOX_W / 2; const hh = BOX_H / 2;
    const pos: DotPos[] = [];
    
    for (const c of countriesWithStatus) {
      const pt = inst.convertToPixel({ geoIndex: 0 }, [c.lng, c.lat]) as [number, number] | null;
      if (!pt) continue;
      const [bdx, bdy] = BASE_OFFSETS[c.iso2] ?? [80, -30];
      // Raw box centre
      let bx = pt[0] + bdx * scale;
      let by = pt[1] + bdy * scale;
      
      // Initial Clamp so box stays inside container
      bx = Math.max(hw + 4, Math.min(cw - hw - 4, bx));
      by = Math.max(hh + 4, Math.min(ch - hh - 4, by));
      
      pos.push({
        iso2: c.iso2, status: c.status, name: c.name,
        px: pt[0], py: pt[1], bx, by,
        meta: metaByCode[c.iso2],
      });
    }

    setDotPositions(pos);
  }, [countriesWithStatus, metaByCode]);

  // Recalculate positions after the map is fully laid out ───────────────────
  useEffect(() => {
    if (!mapReady) return;
    // ECharts geo layout is computed asynchronously after GeoJSON is registered;
    // fire multiple recalculations to catch when it settles.
    const t1 = setTimeout(computePositions, 500);
    const t2 = setTimeout(computePositions, 1200);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [mapReady, computePositions]);

  // Resize observer ──────────────────────────────────────────────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setTimeout(computePositions, 80));
    ro.observe(el);
    return () => ro.disconnect();
  }, [computePositions]);

  // ECharts option ───────────────────────────────────────────────────────────
  const isLight = theme === 'light';

  const option = useMemo(() => {
    if (!mapReady) return {};
    return {
      backgroundColor: 'transparent',
      geo: {
        map: MAP_NAME,
        roam: true,
        scaleLimit: { min: 0.6, max: 8 },
        silent: false,
        itemStyle: {
          areaColor: isLight ? '#e8eef5' : '#1e293b',
          borderColor: isLight ? '#d1dce8' : '#0f172a',
          borderWidth: 0.5,
        },
        emphasis: {
          itemStyle: { areaColor: isLight ? '#dbeafe' : '#273549' },
          label: { show: false },
        },
        select: { disabled: true },
      },
      series: [
        {
          type: 'scatter',
          coordinateSystem: 'geo',
          data: countriesWithStatus.map(c => ({
            value: [c.lng, c.lat],
            name: c.name,
            status: c.status,
            iso2: c.iso2,
          })),
          symbolSize: (_v: any, p: any) => p.data?.status === 'Supported' ? 13 : 9,
          itemStyle: {
            color: (p: any) => p.data?.status === 'Supported' ? '#0d9488' : '#475569',
            borderColor: (p: any) => p.data?.status === 'Supported' ? '#14b8a6' : '#64748b',
            borderWidth: 2,
            shadowBlur: (p: any) => p.data?.status === 'Supported' ? 8 : 0,
            shadowColor: '#0d9488',
          },
          emphasis: {
            itemStyle: {
              color: (p: any) => p.data?.status === 'Supported' ? '#14b8a6' : '#64748b',
            },
          },
          label: { show: false },
          z: 6,
        },
      ],
      tooltip: {
        trigger: 'item',
        backgroundColor: isLight ? '#fff' : '#1e293b',
        borderColor: isLight ? '#cbd5e1' : '#334155',
        textStyle: { color: isLight ? '#0f172a' : '#e2e8f0', fontSize: 12 },
        formatter: (p: any) => `<b>${p.data.name}</b><br/>${p.data.status}`,
      },
    };
  }, [theme, mapReady, isLight, countriesWithStatus]);

  const handleEvents = useMemo(() => ({
    finished: () => setTimeout(computePositions, 600),
    georoam: () => setTimeout(computePositions, 50),
  }), [computePositions]);

  // ─── Render ────────────────────────────────────────────────────────────────

  if (!mapReady) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center text-slate-500 text-sm"
      >
        Loading map…
      </div>
    );
  }

  const scaledBoxW = BOX_W;
  const scaledBoxH = BOX_H;

  return (
    <div ref={containerRef} style={{ position: 'relative', height, overflow: 'hidden' }}>
      {/* ECharts world map */}
      <ReactEChartsCore
        ref={chartRef}
        echarts={echarts}
        option={option}
        style={{ height: '100%', width: '100%' }}
        onEvents={handleEvents}
        notMerge
      />

      {/* ── SVG overlay: L-shaped leader lines ── */}
      {dotPositions.length > 0 && (
        <svg
          style={{
            position: 'absolute', inset: 0,
            width: '100%', height: '100%',
            pointerEvents: 'none', overflow: 'visible',
          }}
        >
          {dotPositions.map(d => {
            const [bdx] = BASE_OFFSETS[d.iso2] ?? [80, -30];
            const isRight = bdx > 0;

            // Connect to the nearest edge of the box
            const edgeX = isRight ? d.bx - scaledBoxW / 2 : d.bx + scaledBoxW / 2;
            const edgeY = d.by;          // box vertical centre

            // L-shape: dot → horizontal run → vertical drop to box centre
            const color = d.status === 'Supported' ? '#0d9488' : '#475569';
            const dash = d.status === 'Scheduled' ? '5 3' : undefined;

            return (
              <g key={d.iso2}>
                {/* dot halo */}
                <circle
                  cx={d.px} cy={d.py} r={d.status === 'Supported' ? 7 : 5}
                  fill="none" stroke={color} strokeWidth={1} opacity={0.35}
                />
                {/* polyline: dot → elbow → box edge */}
                <polyline
                  points={`${d.px},${d.py} ${edgeX},${d.py} ${edgeX},${edgeY}`}
                  fill="none"
                  stroke={color}
                  strokeWidth={1.2}
                  strokeDasharray={dash}
                  opacity={0.75}
                />
                {/* small filled dot at country location */}
                <circle
                  cx={d.px} cy={d.py} r={d.status === 'Supported' ? 4 : 3}
                  fill={color}
                />
              </g>
            );
          })}
        </svg>
      )}

      {/* ── Country info boxes ── */}
      {dotPositions.map(d => {
        const isSupported = d.status === 'Supported';
        const accentColor = isSupported ? '#0d9488' : '#475569';
        const borderColor = isSupported ? '#0d9488' : '#334155';
        const href = isSupported ? `/countries/${d.iso2.toLowerCase()}/` : undefined;

        const boxStyles: React.CSSProperties = {
          position: 'absolute',
          left: d.bx - scaledBoxW / 2,
          top:  d.by - scaledBoxH / 2,
          width: scaledBoxW,
          minHeight: scaledBoxH,
          background: isLight ? '#fff' : '#1e293b',
          border: `1px solid ${borderColor}`,
          borderRadius: 7,
          padding: '6px 10px',
          zIndex: 10,
          lineHeight: 1.4,
          textDecoration: 'none',
          color: isLight ? '#0f172a' : '#e2e8f0',
          cursor: isSupported ? 'pointer' : 'default',
          boxShadow: isSupported
            ? `0 2px 12px ${accentColor}44`
            : `0 1px 4px rgba(0,0,0,0.25)`,
          transition: 'box-shadow 0.15s ease',
          display: 'block',
          userSelect: 'none',
        };

        const inner = (
          <>
            <div style={{
              fontWeight: 600, fontSize: 12,
              display: 'flex', alignItems: 'center', gap: 5, marginBottom: 3,
              color: isLight ? '#0f172a' : '#f1f5f9',
            }}>
              <img
                src={`https://flagcdn.com/w40/${d.iso2.toLowerCase()}.png`}
                alt={d.name}
                style={{ width: 20, height: 14, objectFit: 'cover', borderRadius: 2, flexShrink: 0 }}
              />
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {d.name}
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{
                display: 'inline-block',
                background: isSupported ? accentColor : 'transparent',
                border: `1px solid ${borderColor}`,
                borderRadius: 4,
                padding: '1px 6px',
                fontSize: 10,
                color: isSupported ? '#fff' : '#64748b',
                fontWeight: 500,
              }}>
                {d.status}
              </span>
            </div>
          </>
        );

        return href ? (
          <a key={d.iso2} href={href} style={boxStyles}>{inner}</a>
        ) : (
          <div key={d.iso2} style={boxStyles}>{inner}</div>
        );
      })}
    </div>
  );
}
