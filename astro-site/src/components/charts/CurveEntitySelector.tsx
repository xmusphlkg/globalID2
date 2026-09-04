import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  findLatestValue,
  getMetricValues,
  type CurveEntityType,
  type CurveSelectionMode,
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
  selectionMode: CurveSelectionMode;
  onQueryChange: (query: string) => void;
  onToggle: (id: string) => void;
  onReset: () => void;
}

function formatValue(value: number | null | undefined, digits = 0) {
  if (value == null || Number.isNaN(value)) return '—';
  return digits > 0 ? value.toFixed(digits) : value.toLocaleString();
}

type SortMode = 'default' | 'latest' | 'total' | 'name';

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
  selectionMode,
  onQueryChange,
  onToggle,
  onReset,
}: Props) {
  const isCompact = density === 'compact';
  const [sortMode, setSortMode] = useState<SortMode>('default');
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
  const displayedIds = useMemo(() => {
    const candidates = showSelectedOnly
      ? visibleIds.filter((id) => activeIdSet.has(id))
      : visibleIds;
    const rankById = new Map(eligibleIds.map((id, index) => [id, index]));
    const latestValue = (id: string) => findLatestValue(getMetricValues(series[id], metric)) ?? -Infinity;
    return [...candidates].sort((left, right) => {
      const selectedOrder = Number(activeIdSet.has(right)) - Number(activeIdSet.has(left));
      if (selectedOrder !== 0) return selectedOrder;
      if (sortMode === 'latest') return latestValue(right) - latestValue(left);
      if (sortMode === 'total') return (series[right]?.total_cases ?? 0) - (series[left]?.total_cases ?? 0);
      if (sortMode === 'name') {
        const leftName = lang === 'zh' ? series[left]?.name_zh : series[left]?.name_en;
        const rightName = lang === 'zh' ? series[right]?.name_zh : series[right]?.name_en;
        return (leftName ?? '').localeCompare(rightName ?? '', lang === 'zh' ? 'zh' : 'en');
      }
      return (rankById.get(left) ?? 0) - (rankById.get(right) ?? 0);
    });
  }, [activeIdSet, eligibleIds, lang, metric, series, showSelectedOnly, sortMode, visibleIds]);

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
                ? selectionMode === 'single'
                  ? `搜索并选择一个${entityName.zh}查看。`
                  : `可搜索并勾选多个${entityName.zh}进行比较。`
                : selectionMode === 'single'
                  ? `Search and select one ${entityName.en} to inspect.`
                  : `Search and select multiple ${entityName.enPlural} to compare.`}
            </div>
          )}
        </div>
        <span className="chart-chip whitespace-nowrap">
          {lang === 'zh' ? `已选 ${activeIds.length}` : `${activeIds.length} selected`}
        </span>
      </div>

      <input
        id={`curve-${entityType}-search`}
        name={`curve-${entityType}-search`}
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
        <div className="chart-selector-sort" role="group" aria-label={lang === 'zh' ? '排序方式' : 'Sort items'}>
          {([
            ['default', lang === 'zh' ? '默认' : 'Default'],
            ['latest', lang === 'zh' ? '最新值' : 'Latest'],
            ['total', lang === 'zh' ? '累计' : 'Total'],
            ['name', lang === 'zh' ? '名称' : 'Name'],
          ] as Array<[SortMode, string]>).map(([mode, label]) => (
            <button
              key={mode}
              type="button"
              onClick={() => setSortMode(mode)}
              aria-pressed={sortMode === mode}
              className={`chart-toggle chart-toggle-small ${sortMode === mode ? 'chart-toggle-active' : ''}`}
            >
              {label}
            </button>
          ))}
        </div>
        {selectionMode === 'multiple' && activeIds.length > 0 && (
          <button
            type="button"
            onClick={() => setShowSelectedOnly((current) => !current)}
            aria-pressed={showSelectedOnly}
            className={`chart-toggle chart-toggle-small ${showSelectedOnly ? 'chart-toggle-active' : ''}`}
          >
            {lang === 'zh' ? '仅已选' : 'Selected'}
          </button>
        )}
        {selectionMode === 'multiple' && activeIds.length > 1 && (
          <button type="button" onClick={onReset} className="chart-toggle chart-toggle-small">
            {lang === 'zh' ? '清除比较' : 'Clear comparison'}
          </button>
        )}
      </div>

      <div className={`chart-sidebar-list ${isCompact ? 'chart-sidebar-list-compact' : ''}`}>
        {displayedIds.length === 0 ? (
          <div className="chart-sidebar-empty">
            {lang === 'zh'
              ? `没有匹配的${entityName.zh}。`
              : `No ${entityName.enPlural} matched your search.`}
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
                    name={`curve-${entityType}-selection`}
                    value={id}
                    type={selectionMode === 'single' ? 'radio' : 'checkbox'}
                    checked={isActive}
                    onChange={() => onToggle(id)}
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
                        {lang === 'zh' ? '最新值' : 'Latest'} {formatValue(latestValue, ['incidence_rates', 'trend_index'].includes(metric) ? 2 : 0)}
                        {' · '}
                        {lang === 'zh' ? '累计' : 'Total'} {totalCases.toLocaleString()}
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
