import React, { useEffect, useState } from 'react';
import EpidemicCurve from './EpidemicCurve';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadDiseaseDataset, type DiseaseDatasetSeriesEntry } from './diseaseDataset';

interface Props {
  dataUrl?: string;
  topN?: number;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
}

export default function DiseaseCountryCurve({ dataUrl, topN = 10, height = 380, sourceMeta = null }: Props) {
  const [series, setSeries] = useState<Record<string, DiseaseDatasetSeriesEntry>>({});
  const [loadError, setLoadError] = useState(false);
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });

  useEffect(() => {
    if (!dataUrl) return;

    let cancelled = false;
    loadDiseaseDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setSeries(dataset.country_series ?? {});
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
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载失败' : 'Failed to load chart data'}
      </div>
    );
  }

  if (Object.keys(series).length === 0) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载中' : 'Loading chart data'}
      </div>
    );
  }

  return (
    <EpidemicCurve
      series={series}
      topN={topN}
      height={height}
      sourceMeta={sourceMeta}
    />
  );
}
