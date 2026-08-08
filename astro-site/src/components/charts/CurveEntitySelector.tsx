import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  MAX_ACTIVE_SERIES,
  findLatestValue,
  formatTemporalGranularity,
  getMetricValues,
  getSelectedSourceSeries,
  getSeriesGranularity,
  hasPublicProjection,
  type CurveEntityType,
  type CurveSeries,
  type EpidemicMetric,
} from './epidemicCurveModel';

interface Props {
  density?: 'compact' | 'full';
  series: Record<string, CurveSeries>;
  eligibleIds: string[];
  visibleIds: string[];
  activeIds: string[];
  activeIdSet: Set<string>;
  colorById: Map<string, string>;
  metric: EpidemicMetric;
  entityType: CurveEntityType;
  lang: 'en' | 'zh';
  query: string;
  onQueryChange: (query: string) => void;
  onToggle: (id: string) => void;
  onReset: () => void;
  onSelectVisible: () => void;
  defaultCount: number;
  selectionLimitHit: boolean;
}

function formatValue(value: number | null | undefined, digits = 0) {
  if (value == null || Number.isNaN(value)) return '—';
  return digits > 0 ? value.toFixed(digits) : value.toLocaleString();
}

export default function CurveEntitySelector({
  density = 'full',
  series,
  eligibleIds,
  visibleIds,
  activeIds,
  activeIdSet,
  colorById,
  metric,
  entityType,
  lang,
  query,
  onQueryChange,
  onToggle,
  onReset,
  onSelectVisible,
  defaultCount,
  selectionLimitHit,
}: Props) {
  const isCompact = density === 'compact';
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);
  const totalCasesRange = useMemo(() => {
    if (eligibleIds.length === 0) return { min: 0, max: 0 };
    return eligibleIds.reduce(
      (range, id) => {
        const total = series[id]?.total_cases ?? 0;
        return {
          min: Math.min(range.min, total),
          max: Math.max(range.max, total),
        };
      },
      { min: Number.POSITIVE_INFINITY, max: Number.NEGATIVE_INFINITY }
    );
  }, [eligibleIds, series]);

  const entityName = entityType === 'country'
    ? { en: 'country', enPlural: 'countries', zh: '国家' }
    : { en: 'disease', enPlural: 'diseases', zh: '疾病' };
  const displayedIds = isCompact && showSelectedOnly
    ? visibleIds.filter((id) => activeIdSet.has(id))
    : visibleIds;

  return (
    <div className={`chart-sidebar ${isCompact ? 'chart-sidebar-compact' : ''}`}>
      <div className="chart-sidebar-header">
        <div>
          <div className="chart-sidebar-title">
            {lang === 'zh' ? `${entityName.zh}筛选` : `${entityName.en[0].toUpperCase()}${entityName.en.slice(1)} filter`}
          </div>
          {!isCompact && (
            <div className="chart-sidebar-copy">
              {lang === 'zh'
                ? `可搜索并勾选${entityName.zh}；最多同时显示 ${MAX_ACTIVE_SERIES} 条序列。`
                : `Search and select ${entityName.enPlural}; up to ${MAX_ACTIVE_SERIES} series can be shown at once.`}
            </div>
          )}
        </div>
        <span className="chart-chip whitespace-nowrap">
          {lang === 'zh' ? `已选 ${activeIds.length}` : `${activeIds.length} selected`}
        </span>
      </div>

      <input
        type="search"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder={lang === 'zh'
          ? `搜索${entityName.zh}…`
          : `Search ${entityName.enPlural}…`}
        aria-label={lang === 'zh'
          ? `搜索${entityName.zh}`
          : `Search ${entityName.enPlural}`}
        className={`site-control-input w-full rounded-none border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 ${isCompact ? 'py-1.5' : 'py-2'}`}
      />

      <div className="chart-toolbar">
        <button type="button" onClick={onReset} className="chart-toggle">
          {lang === 'zh'
            ? `回到前 ${defaultCount}`
            : `Reset to top ${defaultCount}`}
        </button>
        {isCompact && (
          <button
            type="button"
            onClick={() => setShowSelectedOnly((current) => !current)}
            aria-pressed={showSelectedOnly}
            className={`chart-toggle ${showSelectedOnly ? 'chart-toggle-active' : ''}`}
          >
            {showSelectedOnly
              ? (lang === 'zh' ? '显示全部' : 'Show all')
              : (lang === 'zh' ? '仅看已选' : 'Selected only')}
          </button>
        )}
        {isCompact && query.trim() && (
          <button
            type="button"
            onClick={() => onQueryChange('')}
            className="chart-toggle"
          >
            {lang === 'zh' ? '清除搜索' : 'Clear search'}
          </button>
        )}
        {!isCompact && (
          <button
            type="button"
            onClick={onSelectVisible}
            className="chart-toggle"
            disabled={visibleIds.length === 0}
          >
            {lang === 'zh'
              ? `选择当前结果（最多 ${MAX_ACTIVE_SERIES}）`
              : `Select visible (max ${MAX_ACTIVE_SERIES})`}
          </button>
        )}
      </div>

      {selectionLimitHit && (
        <div className="chart-sidebar-warning" role="status">
          {lang === 'zh'
            ? `已保留前 ${MAX_ACTIVE_SERIES} 条结果；请取消一条已选${entityName.zh}后再添加。`
            : `The first ${MAX_ACTIVE_SERIES} results are selected; deselect one ${entityName.en} before adding another.`}
        </div>
      )}

      <div className={`chart-sidebar-list ${isCompact ? 'chart-sidebar-list-compact' : ''}`}>
        {displayedIds.length === 0 ? (
          <div className="chart-sidebar-empty">
            {showSelectedOnly
              ? (lang === 'zh'
                  ? `当前搜索中没有已选${entityName.zh}。`
                  : `No selected ${entityName.enPlural} match the current search.`)
              : (lang === 'zh'
                  ? `没有匹配的${entityName.zh}。`
                  : `No ${entityName.enPlural} matched your search.`)}
          </div>
        ) : (
          displayedIds.map((id) => {
            const item = series[id];
            const isActive = activeIdSet.has(id);
            const color = isActive ? colorById.get(id) : undefined;
            const totalCases = item.total_cases ?? 0;
            const latestValue = isCompact
              ? null
              : findLatestValue(getMetricValues(item, metric));
            const selectedSource = hasPublicProjection(item)
              || (item.selected_series_codes?.length ?? 0) > 0
              ? getSelectedSourceSeries(item)[0]
              : undefined;
            const volumePercent = totalCasesRange.max <= totalCasesRange.min
              ? (totalCasesRange.max === 0 ? 0 : 100)
              : ((totalCases - totalCasesRange.min) / (totalCasesRange.max - totalCasesRange.min)) * 100;
            const itemStyle = isCompact
              ? undefined
              : ({
                  ['--chart-sidebar-volume' as string]: `${Math.max(0, Math.min(100, volumePercent))}%`,
                } as CSSProperties);

            return (
              <label
                key={id}
                className={`chart-sidebar-item ${isCompact ? 'chart-sidebar-item-compact' : ''} ${isActive ? 'chart-sidebar-item-active' : ''}`}
                style={itemStyle}
              >
                <div className="chart-sidebar-item-inner flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={isActive}
                    onChange={() => onToggle(id)}
                    disabled={!isActive && activeIds.length >= MAX_ACTIVE_SERIES}
                    className="chart-sidebar-checkbox mt-1"
                    style={color ? { accentColor: color } : undefined}
                  />
                  <span
                    aria-hidden="true"
                    className={`chart-sidebar-swatch ${color ? 'chart-sidebar-swatch-active' : ''}`}
                    style={color ? { backgroundColor: color, borderColor: color } : undefined}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="chart-sidebar-name">
                      {lang === 'zh' ? item.name_zh : item.name_en}
                      {isCompact && entityType === 'country' && (
                        <span className="chart-sidebar-compact-total">
                          {' '}({totalCases.toLocaleString()})
                        </span>
                      )}
                    </div>
                    {!isCompact && (
                      <div className="chart-sidebar-meta">
                        {lang === 'zh' ? '最新值' : 'Latest'} {formatValue(latestValue, metric === 'incidence_rates' ? 2 : 0)}
                        {' · '}
                        {lang === 'zh' ? '累计' : 'Total'} {totalCases.toLocaleString()}
                        {' · '}
                        {formatTemporalGranularity(getSeriesGranularity(item), lang)}
                      </div>
                    )}
                    {isActive && selectedSource && (
                      <div className="chart-sidebar-meta">
                        {lang === 'zh' ? '报告粒度' : 'Grain'} {formatTemporalGranularity(selectedSource.temporal_granularity, lang)}
                        {' · '}
                        {lang === 'zh' ? '报告口径' : 'Basis'} {(selectedSource.reporting_basis ?? 'unknown').replaceAll('_', ' ')}
                        {' · '}
                        {lang === 'zh' ? '可用状态' : 'Availability'} {(selectedSource.availability_status ?? 'unknown').replaceAll('_', ' ')}
                      </div>
                    )}
                  </div>
                </div>
              </label>
            );
          })
        )}
      </div>
    </div>
  );
}
