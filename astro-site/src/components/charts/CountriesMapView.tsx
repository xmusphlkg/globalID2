// src/components/charts/CountriesMapView.tsx
// Flat world map with SVG leader lines (折线) connecting each country dot to an info box.
// Countries and coverage status come from the shared country coverage registry.

import React, {
  useRef, useState, useEffect, useCallback, useMemo,
} from 'react';
import ReactEChartsCore from '../../lib/echartsReact';
import echarts from '../../lib/echartsMap';
import { getFlagAssetPath } from '../../lib/country-flag';
import {
  COUNTRY_COVERAGE,
  getCoverageDisplayName,
  getCoverageLabelOffset,
  hasCountryDataSnapshot,
  resolveCoverageStatus,
  type CoverageStatus,
} from '../../lib/country-coverage';

const MAP_NAME = 'world-countries-lnglat';
const LOCAL_WORLD_MAP_URL = '/data/world.json';
const BOX_W = 138;
const BOX_H = 42;

function getGeoJsonBounds(geoJson: any) {
  const bounds = {
    minX: Infinity,
    maxX: -Infinity,
    minY: Infinity,
    maxY: -Infinity,
  };

  const visit = (value: any) => {
    if (Array.isArray(value) && typeof value[0] === 'number' && typeof value[1] === 'number') {
      bounds.minX = Math.min(bounds.minX, value[0]);
      bounds.maxX = Math.max(bounds.maxX, value[0]);
      bounds.minY = Math.min(bounds.minY, value[1]);
      bounds.maxY = Math.max(bounds.maxY, value[1]);
      return;
    }

    if (Array.isArray(value)) value.forEach(visit);
  };

  geoJson?.features?.forEach((feature: any) => visit(feature.geometry?.coordinates));
  return bounds;
}

function isLngLatWorldGeoJson(geoJson: any) {
  const { minX, maxX, minY, maxY } = getGeoJsonBounds(geoJson);
  return (
    Number.isFinite(minX)
    && minX >= -180.5
    && maxX <= 180.5
    && minY >= -90.5
    && maxY <= 90.5
  );
}

async function fetchLngLatWorldGeoJson() {
  let lastError: unknown;

  for (const url of [LOCAL_WORLD_MAP_URL]) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const geoJson = await response.json();
      if (isLngLatWorldGeoJson(geoJson)) return geoJson;
      throw new Error(`World map is not lng/lat GeoJSON: ${url}`);
    } catch (error) {
      lastError = error;
    }
  }

  throw lastError;
}

// ─── Types ────────────────────────────────────────────────────────────────────

interface MetaCountry {
  code: string;
  name: string;
  total_cases: number;
  total_deaths: number;
  disease_count: number;
  data_available?: boolean;
  record_count?: number;
  date_range?: { start?: string | null; end?: string | null } | null;
}

interface DotPos {
  iso2: string;
  status: CoverageStatus;
  statusLabel: string;
  name: string;
  px: number; py: number;   // dot pixel position (chart-relative)
  bx: number; by: number;   // box centre pixel position
  meta?: MetaCountry;
}

interface Props {
  metaCountries?: MetaCountry[];
  height?: number;
  initialLanguage?: 'en' | 'zh';
}

type CountryFilterWindow = Window & {
  __globalIdCountryFilterCodes?: string[];
};

// ─── Component ────────────────────────────────────────────────────────────────

export default function CountriesMapView({ metaCountries = [], height = 450, initialLanguage = 'en' }: Props) {
  const chartRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  const [lang] = useState<'en' | 'zh'>(initialLanguage);
  const [mapReady, setMapReady] = useState(false);
  const [dotPositions, setDotPositions] = useState<DotPos[]>([]);
  const [visibleCountryCodes, setVisibleCountryCodes] = useState<string[] | null>(() => {
    if (typeof window === 'undefined') return null;
    return (window as CountryFilterWindow).__globalIdCountryFilterCodes ?? null;
  });

  // Build meta lookup
  const metaByCode = useMemo(
    () => Object.fromEntries(metaCountries.map(m => [m.code.toUpperCase(), m])),
    [metaCountries],
  );

  const countriesWithStatus = useMemo(
    () => COUNTRY_COVERAGE.map((c) => {
      const meta = metaByCode[c.code];
      const status = resolveCoverageStatus(c, hasCountryDataSnapshot(meta));
      return {
        iso2: c.code,
        lat: c.lat,
        lng: c.lng,
        name: getCoverageDisplayName(c, lang, meta?.name),
        status,
        statusLabel: lang === 'zh'
          ? status === 'Supported' ? '已支持' : '规划中'
          : status,
      };
    }),
    [lang, metaByCode],
  );

  const displayedCountries = useMemo(() => {
    if (visibleCountryCodes === null) return countriesWithStatus;
    const visible = new Set(visibleCountryCodes.map(code => code.toUpperCase()));
    return countriesWithStatus.filter(country => visible.has(country.iso2));
  }, [countriesWithStatus, visibleCountryCodes]);

  useEffect(() => {
    const handleCountryFilter = (event: Event) => {
      const codes = (event as CustomEvent<{ codes?: string[] }>).detail?.codes;
      setVisibleCountryCodes(Array.isArray(codes) ? codes : null);
    };
    window.addEventListener('globalid:country-filter', handleCountryFilter);
    return () => window.removeEventListener('globalid:country-filter', handleCountryFilter);
  }, []);

  // Theme detection ─────────────────────────────────────────────────────────
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const update = () => {
      setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    };
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
  }, []);

  // Load world GeoJSON ───────────────────────────────────────────────────────
  useEffect(() => {
    if (echarts.getMap(MAP_NAME)) { setMapReady(true); return; }

    let cancelled = false;
    fetchLngLatWorldGeoJson()
      .then((gj) => {
        if (cancelled) return;
        echarts.registerMap(MAP_NAME, gj);
        setMapReady(true);
      })
      .catch((error) => {
        console.error('Unable to load lng/lat world map for countries view', error);
      });

    return () => { cancelled = true; };
  }, []);

  // Convert geo → pixel and place boxes ─────────────────────────────────────
  const computePositions = useCallback(() => {
    const inst = chartRef.current?.getEchartsInstance?.();
    if (!inst) return;

    const rect = containerRef.current?.getBoundingClientRect();
    const cw = rect?.width ?? 900;
    const ch = rect?.height ?? 450;
    const scale = Math.max(0.72, Math.min(1.14, cw / 1100));

    const hw = BOX_W / 2; const hh = BOX_H / 2;
    const pos: DotPos[] = [];
    
    for (const c of displayedCountries) {
      const pt = inst.convertToPixel({ geoIndex: 0 }, [c.lng, c.lat]) as [number, number] | null;
      if (!pt) continue;
      const [bdx, bdy] = getCoverageLabelOffset(c.iso2);
      // Raw box centre
      let bx = pt[0] + bdx * scale;
      let by = pt[1] + bdy * scale;
      
      // Initial Clamp so box stays inside container
      bx = Math.max(hw + 4, Math.min(cw - hw - 4, bx));
      by = Math.max(hh + 4, Math.min(ch - hh - 4, by));
      
      pos.push({
        iso2: c.iso2, status: c.status, statusLabel: c.statusLabel, name: c.name,
        px: pt[0], py: pt[1], bx, by,
        meta: metaByCode[c.iso2],
      });
    }

    setDotPositions(pos);
  }, [displayedCountries, metaByCode]);

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
  const palette = isLight
    ? {
        mapBg: 'linear-gradient(180deg, #eef4fa 0%, #dde8f2 100%)',
        areaColor: '#fbfdff',
        areaBorder: '#b6c8d8',
        areaHover: '#e4edf5',
        tooltipBg: '#ffffff',
        tooltipBorder: '#c7d7e6',
        tooltipText: '#17304d',
        supported: '#0d6d8c',
        supportedBorder: '#1c86aa',
        scheduled: '#7d8d9f',
        scheduledBorder: '#9aacbd',
        boxBg: '#ffffff',
        boxText: '#17304d',
        boxTextMuted: '#657b92',
        boxSupportedBorder: '#a7c2d5',
        boxScheduledBorder: '#c8d6e2',
      }
    : {
        mapBg: 'linear-gradient(180deg, #152233 0%, #111b28 100%)',
        areaColor: '#1b2a3c',
        areaBorder: '#102033',
        areaHover: '#25374d',
        tooltipBg: '#162334',
        tooltipBorder: '#304156',
        tooltipText: '#e2e8f0',
        supported: '#0d9488',
        supportedBorder: '#14b8a6',
        scheduled: '#475569',
        scheduledBorder: '#64748b',
        boxBg: '#1e293b',
        boxText: '#f1f5f9',
        boxTextMuted: '#94a3b8',
        boxSupportedBorder: '#0d9488',
        boxScheduledBorder: '#334155',
      };

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
          areaColor: palette.areaColor,
          borderColor: palette.areaBorder,
          borderWidth: 0.5,
        },
        emphasis: {
          itemStyle: { areaColor: palette.areaHover },
          label: { show: false },
        },
        select: { disabled: true },
      },
      series: [
        {
          type: 'scatter',
          coordinateSystem: 'geo',
          data: displayedCountries.map(c => ({
            value: [c.lng, c.lat],
            name: c.name,
            status: c.status,
            statusLabel: c.statusLabel,
            iso2: c.iso2,
          })),
          symbolSize: (_v: any, p: any) => p.data?.status === 'Supported' ? 13 : 9,
          itemStyle: {
            color: (p: any) => p.data?.status === 'Supported' ? palette.supported : palette.scheduled,
            borderColor: (p: any) => p.data?.status === 'Supported' ? palette.supportedBorder : palette.scheduledBorder,
            borderWidth: 2,
            shadowBlur: (p: any) => p.data?.status === 'Supported' ? 10 : 0,
            shadowColor: palette.supported,
          },
          emphasis: {
            itemStyle: {
              color: (p: any) => p.data?.status === 'Supported' ? palette.supportedBorder : palette.scheduledBorder,
            },
          },
          label: { show: false },
          z: 6,
        },
      ],
      tooltip: {
        trigger: 'item',
        backgroundColor: palette.tooltipBg,
        borderColor: palette.tooltipBorder,
        textStyle: { color: palette.tooltipText, fontSize: 12 },
        formatter: (p: any) => `
          <div style="display:flex;align-items:center;gap:7px">
            <img src="${getFlagAssetPath(p.data?.iso2)}" alt="" width="24" height="18" style="width:24px;height:18px;object-fit:cover;border:1px solid ${palette.tooltipBorder}" />
            <span><b>${p.data.name}</b><br/>${p.data.statusLabel ?? p.data.status}</span>
          </div>`,
      },
    };
  }, [mapReady, displayedCountries, palette]);

  const handleEvents = useMemo(() => ({
    finished: () => setTimeout(computePositions, 600),
    georoam: () => setTimeout(computePositions, 50),
  }), [computePositions]);

  // ─── Render ────────────────────────────────────────────────────────────────

  if (!mapReady) {
    return (
      <div
        style={{ height }}
        className="flex items-center justify-center text-sm text-[rgb(var(--text-muted))]"
      >
        <span className="inline-flex items-center gap-2">
          <svg className="h-4 w-4 animate-spin text-teal-500" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          <span>{lang === 'zh' ? '正在加载地图…' : 'Loading map…'}</span>
        </span>
      </div>
    );
  }

  const scaledBoxW = BOX_W;
  const scaledBoxH = BOX_H;
  // Keep persistent labels for active pipelines only. Scheduled coverage stays
  // visible as map dots with hover tooltips; its source details live in the
  // country cards below the map. This avoids label collisions as the roadmap
  // expands beyond a small handful of countries.
  const labeledPositions = dotPositions.filter(d => d.status === 'Supported');

  return (
    <div
      ref={containerRef}
      style={{
        position: 'relative',
        height,
        overflow: 'hidden',
        background: palette.mapBg,
      }}
    >
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
      {labeledPositions.length > 0 && (
        <svg
          style={{
            position: 'absolute', inset: 0,
            width: '100%', height: '100%',
            pointerEvents: 'none', overflow: 'visible',
          }}
        >
          {labeledPositions.map(d => {
            const [bdx] = getCoverageLabelOffset(d.iso2);
            const isRight = bdx > 0;

            // Connect to the nearest edge of the box
            const edgeX = isRight ? d.bx - scaledBoxW / 2 : d.bx + scaledBoxW / 2;
            const edgeY = d.by;          // box vertical centre

            // L-shape: dot → horizontal run → vertical drop to box centre
            const color = d.status === 'Supported' ? palette.supported : palette.scheduled;
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
      {labeledPositions.map(d => {
        const isSupported = d.status === 'Supported';
        const accentColor = isSupported ? palette.supported : palette.scheduled;
        const borderColor = isSupported ? palette.boxSupportedBorder : palette.boxScheduledBorder;
        const href = isSupported ? `${lang === 'zh' ? '/zh' : ''}/countries/${d.iso2.toLowerCase()}/` : undefined;
        const flagPath = getFlagAssetPath(d.iso2);

        const boxStyles: React.CSSProperties = {
          position: 'absolute',
          left: d.bx - scaledBoxW / 2,
          top:  d.by - scaledBoxH / 2,
          width: scaledBoxW,
          minHeight: scaledBoxH,
          background: palette.boxBg,
          border: `1px solid ${borderColor}`,
          borderRadius: 0,
          padding: '5px 9px',
          zIndex: 10,
          lineHeight: 1.32,
          textDecoration: 'none',
          color: palette.boxText,
          cursor: isSupported ? 'pointer' : 'default',
          boxShadow: isSupported
            ? `0 10px 18px ${accentColor}22`
            : isLight
              ? '0 6px 14px rgba(42, 74, 103, 0.08)'
              : '0 1px 4px rgba(0,0,0,0.25)',
          transition: 'box-shadow 0.15s ease',
          display: 'block',
          userSelect: 'none',
        };

        const inner = (
          <>
            <div style={{
              fontWeight: 650, fontSize: 11.5,
              display: 'flex', alignItems: 'center', gap: 5, marginBottom: 4,
              color: palette.boxText,
            }}>
              <img
                src={flagPath}
                alt=""
                aria-hidden="true"
                width={20}
                height={15}
                style={{ width: 20, height: 15, objectFit: 'cover', border: `1px solid ${borderColor}`, flexShrink: 0 }}
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
                borderRadius: 0,
                padding: '1px 5px',
                fontSize: 9.5,
                color: isSupported ? '#fff' : palette.boxTextMuted,
                fontWeight: 500,
              }}>
                {d.statusLabel}
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
