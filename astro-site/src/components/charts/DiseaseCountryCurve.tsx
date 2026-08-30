import { useEffect, useState } from 'react';
import EpidemicCurve from './EpidemicCurve';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadDiseaseDataset, type DiseaseDatasetSeriesEntry } from './diseaseDataset';

interface Props {
  dataUrl?: string;
  topN?: number;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
  initialLanguage?: 'en' | 'zh';
}

export function nationalJurisdictionSeries(
  series: Record<string, DiseaseDatasetSeriesEntry>,
): Record<string, DiseaseDatasetSeriesEntry> {
  return Object.fromEntries(
    Object.entries(series).filter(([code]) => !code.includes('-')),
  );
}

export default function DiseaseCountryCurve({ dataUrl, topN = 10, height = 380, sourceMeta = null, initialLanguage = 'en' }: Props) {
  const [series, setSeries] = useState<Record<string, DiseaseDatasetSeriesEntry>>({});
  const [loadError, setLoadError] = useState(false);
  const lang = initialLanguage;

  useEffect(() => {
    if (!dataUrl) return;

    let cancelled = false;
    loadDiseaseDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setSeries(nationalJurisdictionSeries(dataset.country_series ?? {}));
        setLoadError(false);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
      });

    return () => {
      cancelled = true;
    };
  }, [dataUrl]);

  if (loadError) {
    return (
      <div className="chart-shell flex items-center justify-center text-[rgb(var(--text-muted))] text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载失败' : 'Failed to load chart data'}
      </div>
    );
  }

  if (Object.keys(series).length === 0) {
    return (
      <div className="chart-loading-shell" role="status" aria-busy="true" aria-label={lang === 'zh' ? '图表数据加载中' : 'Loading chart data'}>
        <div className="chart-loading-toolbar" aria-hidden="true">
          <span className="chart-loading-pill w-24" />
          <span className="chart-loading-pill w-32" />
          <span className="chart-loading-pill w-28" />
        </div>
        <div className="chart-loading-line w-4/5" aria-hidden="true" />
        <div className="chart-loading-panel" style={{ height }} aria-hidden="true" />
      </div>
    );
  }

  return (
    <EpidemicCurve
      series={series}
      topN={topN}
      height={height}
      entityType="country"
      sourceMeta={sourceMeta}
      initialLanguage={initialLanguage}
    />
  );
}
