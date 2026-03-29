import React, { useEffect, useState } from 'react';
import MonthlyBar from './MonthlyBar';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadDiseaseDataset, type DiseaseDatasetMonthlyData } from './diseaseDataset';

interface Props {
  dataUrl?: string;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
}

export default function DiseaseMonthlyBar({ dataUrl, height = 360, sourceMeta = null }: Props) {
  const [monthlyData, setMonthlyData] = useState<DiseaseDatasetMonthlyData | null>(null);
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
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '月度数据加载失败' : 'Failed to load monthly data'}
      </div>
    );
  }

  if (!monthlyData?.months?.length) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '月度数据加载中' : 'Loading monthly data'}
      </div>
    );
  }

  return (
    <MonthlyBar
      data={monthlyData}
      height={height}
      sourceMeta={sourceMeta}
    />
  );
}
