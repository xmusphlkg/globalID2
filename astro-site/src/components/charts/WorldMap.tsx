// src/components/charts/WorldMap.tsx
// ECharts geo choropleth world map — polished dark/light theme.
// GeoJSON served from /data/world.json (public/data/).
// For China-compliant deployment, replace with DataV GeoAtlas world GeoJSON:
//   https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json

import React, { useEffect, useState, useMemo } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echartsMap';

interface WorldMapProps {
  activeCodes: string[];
  height?: number;
}

const PLANNED_COUNTRIES = [
  'US', 'JP', 'KR', 'IN', 'TH', 'VN', 'SG', 'AU',
  'FR', 'DE', 'GB', 'BR', 'ZA', 'NG', 'MX', 'CA',
  'RU', 'ID', 'PK', 'BD',
];

const ISO2_TO_NAME: Record<string, string> = {
  CN: 'China', US: 'United States', JP: 'Japan', KR: 'Korea', IN: 'India',
  TH: 'Thailand', VN: 'Vietnam', SG: 'Singapore', AU: 'Australia', NZ: 'New Zealand',
  FR: 'France', DE: 'Germany', GB: 'United Kingdom', IT: 'Italy', ES: 'Spain',
  BR: 'Brazil', ZA: 'South Africa', NG: 'Nigeria', MX: 'Mexico', CA: 'Canada',
  RU: 'Russia', ID: 'Indonesia', PK: 'Pakistan', BD: 'Bangladesh', PH: 'Philippines',
  MY: 'Malaysia', EG: 'Egypt', TR: 'Turkey', AR: 'Argentina', CO: 'Colombia',
  KE: 'Kenya', ET: 'Ethiopia', GH: 'Ghana', TZ: 'Tanzania', UA: 'Ukraine',
  PL: 'Poland', NL: 'Netherlands', SE: 'Sweden', NO: 'Norway', CH: 'Switzerland',
};

const MAP_NAME = 'world';

export default function WorldMap({ activeCodes, height = 440 }: WorldMapProps) {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  const [mapReady, setMapReady] = useState(false);

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const updateTheme = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (echarts.getMap(MAP_NAME)) { setMapReady(true); return; }
    fetch('/data/world.json')
      .then(r => r.json())
      .then(geoJson => { echarts.registerMap(MAP_NAME, geoJson); setMapReady(true); })
      .catch(() =>
        fetch('https://cdn.jsdelivr.net/npm/echarts@5/map/json/world.json')
          .then(r => r.json())
          .then(geoJson => { echarts.registerMap(MAP_NAME, geoJson); setMapReady(true); })
      );
  }, []);

  const option = useMemo(() => {
    if (!mapReady) return {};
    const isLight = theme === 'light';

    const c = isLight ? {
      ocean:   '#dbeafe',
      land:    '#f1f5f9',
      border:  '#e2e8f0',
      active:  '#0d9488',
      activeBorder: '#0f766e',
      planned: '#3b82f6',
      plannedFill: '#bfdbfe',
      hover:   '#0ea5e9',
      text:    '#334155',
      tooltip: { bg: '#ffffff', border: '#cbd5e1', text: '#0f172a' },
    } : {
      ocean:   '#0f172a',
      land:    '#1e293b',
      border:  '#0f172a',
      active:  '#0d9488',
      activeBorder: '#14b8a6',
      planned: '#1d4ed8',
      plannedFill: '#1e3a5f',
      hover:   '#0ea5e9',
      text:    '#94a3b8',
      tooltip: { bg: '#1e293b', border: '#334155', text: '#e2e8f0' },
    };

    const activeSet = new Set(activeCodes.map(x => x.toUpperCase()));
    const plannedSet = new Set(PLANNED_COUNTRIES.filter(x => !activeSet.has(x)));

    const activeData = [...activeSet]
      .filter(x => ISO2_TO_NAME[x])
      .map(x => ({ name: ISO2_TO_NAME[x], value: 2, iso2: x }));
    const plannedData = [...plannedSet]
      .filter(x => ISO2_TO_NAME[x])
      .map(x => ({ name: ISO2_TO_NAME[x], value: 1, iso2: x }));

    return {
      backgroundColor: c.ocean,
      tooltip: {
        trigger: 'item',
        backgroundColor: c.tooltip.bg,
        borderColor: c.tooltip.border,
        borderWidth: 1,
        padding: [8, 12],
        textStyle: { color: c.tooltip.text, fontSize: 13, fontFamily: 'Inter, system-ui, sans-serif' },
        formatter: (p: any) => {
          if (!p.data) return `<span style="color:${c.tooltip.text}">${p.name}</span>`;
          const icon = p.data.value === 2 ? '🟢' : '🔵';
          const status = p.data.value === 2 ? '<b style="color:#0d9488">Active Coverage</b>' : '<b style="color:#3b82f6">Planned</b>';
          return `${icon} <b>${p.name}</b> (${p.data.iso2})<br/>${status}`;
        },
      },
      visualMap: {
        show: false,
        min: 0, max: 2,
        inRange: { color: [c.land, c.plannedFill, c.active] },
      },
      series: [{
        type: 'map' as const,
        map: MAP_NAME,
        roam: false,
        // Slight zoom/center to give the map breathing room
        layoutCenter: ['50%', '54%'],
        layoutSize: '100%',
        label: { show: false },
        emphasis: {
          label: { show: false },
          itemStyle: {
            areaColor: c.hover,
            borderColor: c.hover,
            borderWidth: 1,
            shadowBlur: isLight ? 8 : 12,
            shadowColor: isLight ? 'rgba(14,165,233,0.3)' : 'rgba(14,165,233,0.5)',
          },
        },
        select: { disabled: true },
        itemStyle: {
          areaColor: c.land,
          borderColor: c.border,
          borderWidth: isLight ? 0.5 : 0.3,
        },
        data: [
          ...activeData.map(d => ({
            ...d,
            itemStyle: {
              areaColor: c.active,
              borderColor: c.activeBorder,
              borderWidth: 1,
              shadowBlur: isLight ? 0 : 8,
              shadowColor: isLight ? 'transparent' : 'rgba(13,148,136,0.6)',
            },
          })),
          ...plannedData.map(d => ({
            ...d,
            itemStyle: {
              areaColor: c.plannedFill,
              borderColor: c.planned,
              borderWidth: isLight ? 0.8 : 0.5,
            },
          })),
        ],
      }],
    };
  }, [mapReady, theme, activeCodes]);

  if (!mapReady) {
    return (
      <div style={{ height }} className="flex items-center justify-center text-slate-500 text-sm rounded-none">
        <span className="inline-flex items-center gap-2">
          <svg className="animate-spin w-4 h-4 text-teal-500" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
          </svg>
          <span data-lang-en="Loading map…" data-lang-zh="正在加载地图…">Loading map…</span>
        </span>
      </div>
    );
  }

  const isLight = theme === 'light';

  return (
    <div className="relative rounded-none overflow-hidden"
      style={{
        background: isLight
          ? 'linear-gradient(135deg, #f0f9ff 0%, #dbeafe 100%)'
          : 'linear-gradient(135deg, #0f172a 0%, #0d1b2e 100%)',
        boxShadow: isLight
          ? 'inset 0 1px 0 rgba(255,255,255,0.6), 0 4px 24px rgba(59,130,246,0.08)'
          : 'inset 0 1px 0 rgba(255,255,255,0.04)',
      }}
    >
      <EChartsReact
        echarts={echarts}
        option={option}
        notMerge
        style={{ width: '100%', height }}
      />
      {/* Legend */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-5 text-xs flex-wrap justify-center">
        <span className="flex items-center gap-2 px-3 py-1.5 rounded-none backdrop-blur-sm"
          style={{ background: isLight ? 'rgba(255,255,255,0.8)' : 'rgba(15,23,42,0.75)', border: `1px solid ${isLight ? '#e2e8f0' : '#334155'}` }}>
          <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: '#0d9488', boxShadow: '0 0 6px rgba(13,148,136,0.7)' }} />
          <span style={{ color: isLight ? '#0f172a' : '#e2e8f0' }} data-lang-en="Active Coverage" data-lang-zh="已覆盖">Active Coverage</span>
        </span>
        <span className="flex items-center gap-2 px-3 py-1.5 rounded-none backdrop-blur-sm"
          style={{ background: isLight ? 'rgba(255,255,255,0.8)' : 'rgba(15,23,42,0.75)', border: `1px solid ${isLight ? '#e2e8f0' : '#334155'}` }}>
          <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: isLight ? '#bfdbfe' : '#1e3a5f', border: `1px solid ${isLight ? '#3b82f6' : '#1d4ed8'}` }} />
          <span style={{ color: isLight ? '#0f172a' : '#e2e8f0' }} data-lang-en="Planned" data-lang-zh="计划中">Planned</span>
        </span>
      </div>
    </div>
  );
}
