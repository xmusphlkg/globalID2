import { useEffect, useState } from 'react';
import MonthlyBar from './MonthlyBar';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadDiseaseDataset, type DiseaseDatasetMonthlyData } from './diseaseDataset';

interface Props {
  dataUrl?: string;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
  initialLanguage?: 'en' | 'zh' | 'fr';
}

export default function DiseaseMonthlyBar({ dataUrl, height = 360, sourceMeta = null, initialLanguage = 'en' }: Props) {
  const [monthlyData, setMonthlyData] = useState<DiseaseDatasetMonthlyData | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const lang = initialLanguage;

  useEffect(() => {
    setLoaded(false);
    setMonthlyData(null);
    if (!dataUrl) {
      setLoaded(true);
      return;
    }

    let cancelled = false;
    loadDiseaseDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setMonthlyData(dataset.global_monthly ?? null);
        setLoadError(false);
        setLoaded(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
        setLoaded(true);
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

  if (!loaded) {
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

  if (!monthlyData?.months?.length ||
      (!monthlyData.cases.some((value) => value !== null) &&
       !monthlyData.deaths.some((value) => value !== null))) {
    return (
      <div className="chart-shell flex items-center justify-center text-[rgb(var(--text-muted))] text-sm min-h-[160px]" role="status">
        {lang === 'zh'
          ? '没有可计算的国家级月度合计；可在上方查看各国家或地区的原始曲线。'
          : lang === 'fr'
            ? 'Aucun sous-total mensuel national calculable ; consultez les courbes par pays ci-dessus.'
            : 'No calculable national monthly subtotal; see the country curves above.'}
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
