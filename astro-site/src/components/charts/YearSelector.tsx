import { useMemo, useState } from 'react';
import type { CSSProperties } from 'react';
import type { YearSummary } from './monthlyBarModel';

interface Props {
  density?: 'compact' | 'full';
  lang: 'en' | 'zh';
  allYears: string[];
  recentYears: string[];
  selectedYearSet: Set<string>;
  visibleYearSummaries: YearSummary[];
  query: string;
  onQueryChange: (query: string) => void;
  onToggleYear: (year: string) => void;
  onSelectRecent: () => void;
  onSelectAll: () => void;
  onSelectVisible: () => void;
  isShowingRecentYears: boolean;
  isShowingAllYears: boolean;
}

function formatValue(value: number) {
  return Number.isFinite(value) ? value.toLocaleString() : '—';
}

export default function YearSelector({
  density = 'full',
  lang,
  allYears,
  recentYears,
  selectedYearSet,
  visibleYearSummaries,
  query,
  onQueryChange,
  onToggleYear,
  onSelectRecent,
  onSelectAll,
  onSelectVisible,
  isShowingRecentYears,
  isShowingAllYears,
}: Props) {
  const isCompact = density === 'compact';
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);
  const displayedSummaries = isCompact && showSelectedOnly
    ? visibleYearSummaries.filter((summary) => selectedYearSet.has(summary.year))
    : visibleYearSummaries;
  const totalRange = useMemo(() => {
    if (visibleYearSummaries.length === 0) return { min: 0, max: 0 };
    const totals = visibleYearSummaries.map((summary) => summary.total);
    return {
      min: Math.min(...totals),
      max: Math.max(...totals),
    };
  }, [visibleYearSummaries]);

  return (
    <div className={`chart-sidebar ${isCompact ? 'chart-sidebar-compact' : ''}`}>
      <div className="chart-sidebar-header">
        <div>
          <div className="chart-sidebar-title">
            {lang === 'zh' ? '年份筛选' : 'Year filter'}
          </div>
          {!isCompact && (
            <div className="chart-sidebar-copy">
              {lang === 'zh'
                ? '按年份搜索和勾选；图表与表格会同步更新。'
                : 'Search and select years; the chart and table stay in sync.'}
            </div>
          )}
        </div>
        <span className="chart-chip whitespace-nowrap">
          {lang === 'zh'
            ? `已选 ${selectedYearSet.size} / ${allYears.length}`
            : `${selectedYearSet.size}/${allYears.length} selected`}
        </span>
      </div>

      <input
        type="search"
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder={lang === 'zh' ? '搜索年份…' : 'Search years…'}
        aria-label={lang === 'zh' ? '搜索年份' : 'Search years'}
        className={`site-control-input w-full rounded-none border px-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 ${isCompact ? 'py-1.5' : 'py-2'}`}
      />

      <div className="chart-toolbar">
        <button
          type="button"
          onClick={onSelectRecent}
          className={`chart-toggle ${isShowingRecentYears ? 'chart-toggle-active' : ''}`}
          disabled={recentYears.length === 0}
        >
          {lang === 'zh'
            ? `最近 ${recentYears.length} 年`
            : `Last ${recentYears.length}`}
        </button>
        <button
          type="button"
          onClick={onSelectAll}
          className={`chart-toggle ${isShowingAllYears ? 'chart-toggle-active' : ''}`}
          disabled={allYears.length === 0}
        >
          {lang === 'zh' ? '全部年份' : 'All years'}
        </button>
        {isCompact ? (
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
        ) : (
          <button
            type="button"
            onClick={onSelectVisible}
            className="chart-toggle"
            disabled={visibleYearSummaries.length === 0}
          >
            {lang === 'zh' ? '选择当前结果' : 'Select visible'}
          </button>
        )}
        {query.trim() && (
          <button
            type="button"
            onClick={() => onQueryChange('')}
            className="chart-toggle"
          >
            {lang === 'zh' ? '清除搜索' : 'Clear search'}
          </button>
        )}
      </div>

      <div className={`chart-sidebar-list ${isCompact ? 'chart-sidebar-list-compact' : ''}`}>
        {displayedSummaries.length === 0 ? (
          <div className="chart-sidebar-empty">
            {showSelectedOnly
              ? (lang === 'zh' ? '当前搜索中没有已选年份。' : 'No selected years match the current search.')
              : (lang === 'zh' ? '没有匹配的年份。' : 'No years matched your search.')}
          </div>
        ) : (
          displayedSummaries.map((summary) => {
            const isActive = selectedYearSet.has(summary.year);
            const volumePercent = totalRange.max <= totalRange.min
              ? (totalRange.max === 0 ? 0 : 100)
              : ((summary.total - totalRange.min) / (totalRange.max - totalRange.min)) * 100;
            const itemStyle = isCompact
              ? undefined
              : ({
                  ['--chart-sidebar-volume' as string]: `${Math.max(0, Math.min(100, volumePercent))}%`,
                } as CSSProperties);

            return (
              <label
                key={summary.year}
                className={`chart-sidebar-item ${isCompact ? 'chart-sidebar-item-compact' : ''} ${isActive ? 'chart-sidebar-item-active' : ''}`}
                style={itemStyle}
              >
                <div className="chart-sidebar-item-inner flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={isActive}
                    onChange={() => onToggleYear(summary.year)}
                    disabled={isActive && selectedYearSet.size === 1}
                    className="chart-sidebar-checkbox mt-1"
                    style={{ accentColor: summary.color }}
                  />
                  <span
                    aria-hidden="true"
                    className={`chart-sidebar-swatch ${isActive ? 'chart-sidebar-swatch-active' : ''}`}
                    style={{
                      backgroundColor: summary.color,
                      borderColor: summary.color,
                      opacity: isActive ? 1 : 0.5,
                    }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="chart-sidebar-name">
                      {summary.year}
                      {isCompact && (
                        <span className="chart-sidebar-compact-total">
                          {' '}({formatValue(summary.total)})
                        </span>
                      )}
                    </div>
                    {!isCompact && (
                      <div className="chart-sidebar-meta">
                        {lang === 'zh' ? '年度合计' : 'Year total'} {formatValue(summary.total)}
                        {' · '}
                        {lang === 'zh' ? '峰值' : 'Peak'} {summary.peakMonth}
                        {summary.peakMonth !== '—' ? ` (${formatValue(summary.peakValue)})` : ''}
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
