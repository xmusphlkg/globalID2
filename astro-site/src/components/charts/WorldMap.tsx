import React, { useEffect, useRef } from 'react';

interface WorldMapProps {
  /** ISO-2 country codes that are actively covered */
  activeCodes: string[];
  height?: number;
}

// ISO-2 → ISO-3 mapping (only major countries needed)
const ISO2_TO_3: Record<string, string> = {
  CN: 'CHN', US: 'USA', JP: 'JPN', KR: 'KOR', IN: 'IND',
  TH: 'THA', VN: 'VNM', SG: 'SGP', AU: 'AUS', NZ: 'NZL',
  FR: 'FRA', DE: 'DEU', GB: 'GBR', IT: 'ITA', ES: 'ESP',
  BR: 'BRA', ZA: 'ZAF', NG: 'NGA', MX: 'MEX', CA: 'CAN',
  RU: 'RUS', ID: 'IDN', PK: 'PAK', BD: 'BGD', PH: 'PHL',
  MY: 'MYS', EG: 'EGY', TR: 'TUR', AR: 'ARG', CO: 'COL',
  KE: 'KEN', ET: 'ETH', GH: 'GHA', TZ: 'TZA', UA: 'UKR',
  PL: 'POL', NL: 'NLD', SE: 'SWE', NO: 'NOR', CH: 'CHE',
};

// Countries planned for future coverage (ISO-2)
const PLANNED_COUNTRIES = [
  'US', 'JP', 'KR', 'IN', 'TH', 'VN', 'SG', 'AU',
  'FR', 'DE', 'GB', 'BR', 'ZA', 'NG', 'MX', 'CA',
  'RU', 'ID', 'PK', 'BD',
];

export default function WorldMap({ activeCodes, height = 420 }: WorldMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [theme, setTheme] = React.useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'dark';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });

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
    let cancelled = false;

    async function renderMap() {
      // @ts-ignore
      const Plotly = await import('plotly.js-dist-min');
      if (cancelled || !containerRef.current) return;

      const activeSet = new Set(activeCodes.map(c => c.toUpperCase()));
      const plannedSet = new Set(
        PLANNED_COUNTRIES.filter(c => !activeSet.has(c))
      );

      const isLight = theme === 'light';
      const colors = {
        planned: isLight ? '#93c5fd' : '#1e3a5f',
        active: isLight ? '#0d9488' : '#0d9488',
        markerLine: isLight ? '#f8fafc' : '#0f172a',
        land: isLight ? '#e2e8f0' : '#1e293b',
        ocean: isLight ? '#f8fafc' : '#0f172a',
        country: isLight ? '#cbd5e1' : '#1e3a5f',
      };

      // Build arrays for choropleth
      const locations: string[] = [];
      const zValues: number[] = [];
      const hoverTexts: string[] = [];

      // Active countries → value 2
      for (const iso2 of activeSet) {
        const iso3 = ISO2_TO_3[iso2];
        if (!iso3) continue;
        locations.push(iso3);
        zValues.push(2);
        hoverTexts.push(`${iso2} — Active`);
      }

      // Planned countries → value 1
      for (const iso2 of plannedSet) {
        const iso3 = ISO2_TO_3[iso2];
        if (!iso3) continue;
        locations.push(iso3);
        zValues.push(1);
        hoverTexts.push(`${iso2} — Planned`);
      }

      const trace: any = {
        type: 'choropleth',
        locationmode: 'ISO-3',
        locations,
        z: zValues,
        text: hoverTexts,
        hoverinfo: 'text',
        colorscale: [
          [0, colors.planned],
          [0.5, colors.planned],
          [0.5, colors.active],
          [1, colors.active],
        ],
        zmin: 0,
        zmax: 2,
        showscale: false,
        marker: {
          line: {
            color: colors.markerLine,
            width: 0.5,
          },
        },
      };

      const layout: any = {
        geo: {
          showframe: false,
          showcoastlines: false,
          showland: true,
          landcolor: colors.land,
          showocean: true,
          oceancolor: colors.ocean,
          showlakes: false,
          showcountries: true,
          countrycolor: colors.country,
          bgcolor: 'transparent',
          projection: { type: 'natural earth' },
        },
        paper_bgcolor: 'transparent',
        plot_bgcolor: 'transparent',
        margin: { l: 0, r: 0, t: 0, b: 0 },
        height,
        dragmode: false,
      };

      const config: any = {
        displayModeBar: false,
        responsive: true,
        scrollZoom: false,
      };

      await Plotly.newPlot(containerRef.current!, [trace], layout, config);
    }

    renderMap();
    return () => { cancelled = true; };
  }, [activeCodes, height, theme]);

  return (
    <div>
      <div ref={containerRef} style={{ width: '100%', height }} />
      {/* Legend */}
      <div className="flex items-center justify-center gap-6 mt-3 text-xs text-slate-400 flex-wrap">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: '#0d9488' }} />
          <span data-lang-en="Active Coverage" data-lang-zh="已覆盖">Active Coverage</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: theme === 'light' ? '#93c5fd' : '#1e3a5f' }} />
          <span data-lang-en="Planned" data-lang-zh="计划中">Planned</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-3 rounded-sm"
            style={{
              background: theme === 'light' ? '#e2e8f0' : '#1e293b',
              border: `1px solid ${theme === 'light' ? '#cbd5e1' : '#334155'}`,
            }}
          />
          <span data-lang-en="Not Covered" data-lang-zh="暂未覆盖">Not Covered</span>
        </span>
      </div>
    </div>
  );
}
