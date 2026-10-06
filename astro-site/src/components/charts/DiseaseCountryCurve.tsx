import { useEffect, useState } from 'react';
import EpidemicCurve from './EpidemicCurve';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import { loadDiseaseDataset, type DiseaseDatasetSeriesEntry } from './diseaseDataset';
import { availableSubdivisionParents, geographyScopeIds, type CurveGeographyScope } from './curveGeography';
import { useChartLanguage } from './chartPreferences';
import { localizedRegionName } from '../../utils/i18n';

interface Props {
  dataUrl?: string;
  topN?: number;
  height?: number;
  entityIds?: string[];
  sourceMeta?: ChartSourceMeta | null;
  initialLanguage?: 'en' | 'zh' | 'fr';
}

export default function DiseaseCountryCurve({ dataUrl, topN = 10, height = 380, entityIds, sourceMeta = null, initialLanguage = 'en' }: Props) {
  const [series, setSeries] = useState<Record<string, DiseaseDatasetSeriesEntry>>({});
  const [loadError, setLoadError] = useState(false);
  const lang = useChartLanguage(initialLanguage);
  const [scope, setScope] = useState<CurveGeographyScope>('national');

  useEffect(() => {
    setSeries({});
    setLoadError(false);
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

  const subdivisionParents = availableSubdivisionParents(series, entityIds);
  const scopeIds = geographyScopeIds(series, scope, entityIds);
  const isSubdivisionScope = scope.startsWith('subdivisions:');
  const scopeControls = subdivisionParents.length > 0 ? (
    <div className="flex flex-wrap items-center gap-2">
      <label className="chart-metric-select">
        <span>{lang === 'zh' ? '比较范围' : lang === 'fr' ? 'Périmètre' : 'Comparison scope'}</span>
        <select
          className="site-control-input rounded-none border px-2 py-1.5 text-xs"
          aria-label={lang === 'zh' ? '比较范围' : lang === 'fr' ? 'Périmètre' : 'Comparison scope'}
          value={scope}
          onChange={(event) => setScope(event.target.value as CurveGeographyScope)}
        >
          <option value="national">{lang === 'zh' ? '国家／地区' : lang === 'fr' ? 'Pays et régions' : 'Countries / regions'}</option>
          {subdivisionParents.map((parent) => (
            <option key={parent} value={`subdivisions:${parent}`}>
              {parent === 'CN'
                ? (lang === 'zh' ? '中国各省份' : lang === 'fr' ? 'Provinces de Chine' : 'China provinces')
                : parent === 'AU'
                  ? (lang === 'zh' ? '澳大利亚各州／领地' : lang === 'fr' ? 'États et territoires australiens' : 'Australia states / territories')
                  : parent === 'BR'
                    ? (lang === 'zh' ? '巴西各州／联邦区' : lang === 'fr' ? 'États et district fédéral du Brésil' : 'Brazil states / federal district')
                  : `${localizedRegionName(lang, parent)} · ${lang === 'zh' ? '省级地区' : lang === 'fr' ? 'subdivisions' : 'subdivisions'}`}
            </option>
          ))}
          <option value="mixed">{lang === 'zh' ? '省份与国家 · 发病率比较' : lang === 'fr' ? 'Provinces et pays · taux' : 'Provinces & countries · incidence rates'}</option>
        </select>
      </label>
      <span className="chart-date-range">
        {isSubdivisionScope
          ? scope === 'subdivisions:BR'
            ? (lang === 'zh' ? '按居住州和通知月汇总；SINAN 通知记录不等同于全部确诊病例。' : lang === 'fr' ? 'Agrégation par État de résidence et mois de notification ; les notifications SINAN ne sont pas toutes des cas confirmés.' : 'By residence state and notification month; SINAN notifications are not all confirmed cases.')
            : (lang === 'zh' ? `当前疾病有 ${scopeIds.length} 个省级地区报告，可全选或勾选比较。` : lang === 'fr' ? `${scopeIds.length} subdivisions déclarantes pour cette maladie.` : `${scopeIds.length} reporting subdivisions for this disease; select all or choose individual locations.`)
          : scope === 'mixed'
            ? (lang === 'zh' ? '勾选省份和国家；发病率使用各地区人口与来源报告期间，不作年化。' : lang === 'fr' ? 'Choisissez des provinces et pays. Les taux utilisent la population locale et la période source, sans annualisation.' : 'Choose provinces and countries. Rates use each location’s population and source period, without annualization.')
            : (lang === 'zh' ? '切换范围可比较同一疾病的省级数据。' : lang === 'fr' ? 'Changez le périmètre pour comparer les subdivisions.' : 'Change scope to compare subdivisions for this disease.')}
      </span>
    </div>
  ) : null;

  return (
    <EpidemicCurve
      key={scope}
      series={series}
      topN={isSubdivisionScope ? scopeIds.length : scope === 'mixed' ? 1 : topN}
      entityIds={scopeIds}
      height={height}
      entityType={scope === 'national' ? 'country' : 'location'}
      initialAnalysisMode={scope === 'national' ? 'monitor' : 'compare'}
      initialMetric={scope === 'mixed' ? 'incidence_rates' : 'cases'}
      allowSelectAll={isSubdivisionScope}
      contextControls={scopeControls}
      sourceMeta={sourceMeta}
      initialLanguage={initialLanguage}
    />
  );
}
