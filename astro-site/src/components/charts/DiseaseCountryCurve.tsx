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
      <div className="chart-shell flex items-center justify-center text-[rgb(var(--text-muted))] text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载失败' : 'Failed to load chart data'}
      </div>
    );
  }

  if (Object.keys(series).length === 0) {
    return (
      <div className="chart-shell flex items-center justify-center text-[rgb(var(--text-muted))] text-sm min-h-[160px]">
        {lang === 'zh' ? '图表数据加载中' : 'Loading chart data'}
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
