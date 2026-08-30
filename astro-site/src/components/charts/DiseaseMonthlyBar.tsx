import { useEffect, useState } from 'react';
import MonthlyBar from './MonthlyBar';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadDiseaseDataset, type DiseaseDatasetMonthlyData } from './diseaseDataset';

interface Props {
  dataUrl?: string;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
  initialLanguage?: 'en' | 'zh';
}

export default function DiseaseMonthlyBar({ dataUrl, height = 360, sourceMeta = null, initialLanguage = 'en' }: Props) {
  const [monthlyData, setMonthlyData] = useState<DiseaseDatasetMonthlyData | null>(null);
  const [loadError, setLoadError] = useState(false);
  const lang = initialLanguage;

  useEffect(() => {
    if (!dataUrl) return;

    let cancelled = false;
    loadDiseaseDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setMonthlyData(dataset.global_monthly ?? null);
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
        {lang === 'zh' ? '月度数据加载失败' : 'Failed to load monthly data'}
      </div>
    );
  }

  if (!monthlyData?.months?.length) {
    return (
      <div className="chart-loading-shell" role="status" aria-busy="true" aria-label={lang === 'zh' ? '月度数据加载中' : 'Loading monthly data'}>
        <div className="chart-loading-toolbar" aria-hidden="true">
          <span className="chart-loading-pill w-20" />
          <span className="chart-loading-pill w-28" />
          <span className="chart-loading-pill w-24" />
        </div>
        <div className="chart-loading-line w-3/5" aria-hidden="true" />
        <div className="chart-loading-panel" style={{ height }} aria-hidden="true" />
      </div>
    );
  }

  return (
    <MonthlyBar
      data={monthlyData}
      height={height}
      sourceMeta={sourceMeta}
      initialLanguage={initialLanguage}
    />
  );
}
