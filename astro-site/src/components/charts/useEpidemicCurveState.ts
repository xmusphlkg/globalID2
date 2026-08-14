import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import {
  INITIAL_CURVE_VIEW_STATE,
  collectDates,
  epidemicCurveViewReducer,
  getMetricValues,
  reconcileDateWindow,
  supportsWeeklyEquivalent,
  type CurveSelectionMode,
  type CurveSeries,
  type DateWindow,
  type EpidemicMetric,
} from './epidemicCurveModel';

interface Options {
  series: Record<string, CurveSeries>;
  entityIds?: string[];
  topN: number;
  caseOnlyEntityIds?: string[];
  initialSelectionMode?: CurveSelectionMode;
}

export function useEpidemicCurveState({
  series,
  entityIds,
  topN,
  caseOnlyEntityIds = [],
  initialSelectionMode = 'single',
}: Options) {
  const [viewState, dispatchView] = useReducer(
    epidemicCurveViewReducer,
    INITIAL_CURVE_VIEW_STATE
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [selectionMode, setSelectionModeState] = useState<CurveSelectionMode>(initialSelectionMode);
  const [query, setQuery] = useState('');

  const eligibleIds = useMemo(() => {
    const ids = Array.from(new Set(entityIds ?? Object.keys(series)));
    return ids
      .filter((id) => id in series && series[id]?.category !== 'Summary')
      .sort((a, b) => (series[b]?.total_cases ?? 0) - (series[a]?.total_cases ?? 0));
  }, [entityIds, series]);
  const eligibleRank = useMemo(
    () => new Map(eligibleIds.map((id, index) => [id, index])),
    [eligibleIds]
  );
  const defaultIds = useMemo(
    () => eligibleIds.slice(0, Math.max(1, topN)),
    [eligibleIds, topN]
  );

  useEffect(() => {
    setSelectedIds((current) => {
      const retained = current
        .filter((id) => eligibleRank.has(id));
      const next = retained.length > 0 ? retained : defaultIds;
      return selectionMode === 'single' ? next.slice(0, 1) : next;
    });
  }, [defaultIds, eligibleRank, selectionMode]);

  const activeIds = useMemo(() => {
    const source = selectedIds.length > 0 ? selectedIds : defaultIds;
    return source
      .filter((id) => eligibleRank.has(id))
      .sort((a, b) => (eligibleRank.get(a) ?? 0) - (eligibleRank.get(b) ?? 0));
  }, [defaultIds, eligibleRank, selectedIds]);
  const activeIdSet = useMemo(() => new Set(activeIds), [activeIds]);
  const caseOnlyEntityIdSet = useMemo(
    () => new Set(caseOnlyEntityIds),
    [caseOnlyEntityIds]
  );
  const activeDates = useMemo(
    () => collectDates(
      series,
      activeIds.filter((id) => {
        const values = getMetricValues(series[id], viewState.metric);
        return values.length === (series[id]?.dates ?? []).length
          && values.some((value) => value != null);
      })
    ),
    [activeIds, series, viewState.metric]
  );
  const dateWindow = useMemo(
    () => reconcileDateWindow(viewState.dateWindow, activeDates),
    [activeDates, viewState.dateWindow]
  );

  useEffect(() => {
    dispatchView({ type: 'dateDomainChanged', dates: activeDates });
  }, [activeDates]);

  const availableMetrics = useMemo(() => {
    const metrics: EpidemicMetric[] = ['cases'];
    const hasHistoricalReference = activeIds.some((id) => (
      getMetricValues(series[id], 'historical_index').some((value) => value != null)
    ));
    if (hasHistoricalReference) metrics.push('historical_index');
    metrics.push('trend_index');
    const hasCompatibleWeeklyValues = activeIds.some((id) => (
      supportsWeeklyEquivalent(series[id])
    ));
    if (hasCompatibleWeeklyValues) metrics.push('weekly_equiv_cases');
    if (activeIds.some((id) => caseOnlyEntityIdSet.has(id))) return metrics;
    const hasDeaths = activeIds.some((id) => (
      (series[id]?.deaths ?? []).some((value) => (value ?? 0) > 0)
    ));
    if (hasDeaths) metrics.push('deaths');
    const hasIncidence = activeIds.some((id) => (
      (series[id]?.incidence_rates ?? []).some((value) => value != null)
    ));
    if (hasIncidence) metrics.push('incidence_rates');
    return metrics;
  }, [activeIds, caseOnlyEntityIdSet, series]);

  useEffect(() => {
    if (!availableMetrics.includes(viewState.metric)) {
      dispatchView({ type: 'metricChanged', metric: 'cases' });
    }
  }, [availableMetrics, viewState.metric]);

  const visibleIds = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return eligibleIds;
    return eligibleIds.filter((id) => {
      const item = series[id];
      return (item.name_en ?? '').toLowerCase().includes(normalizedQuery)
        || (item.name_zh ?? '').toLowerCase().includes(normalizedQuery);
    });
  }, [eligibleIds, query, series]);

  const setMetric = useCallback((metric: EpidemicMetric) => {
    dispatchView({ type: 'metricChanged', metric });
  }, []);

  const setDateWindow = useCallback((nextDateWindow: DateWindow) => {
    dispatchView({ type: 'dateWindowChanged', dateWindow: nextDateWindow });
  }, []);

  const setSelectionMode = useCallback((mode: CurveSelectionMode) => {
    setSelectionModeState(mode);
    if (mode === 'single') {
      setSelectedIds((current) => {
        const source = current.length > 0 ? current : defaultIds;
        return source.length > 0 ? [source[0]] : [];
      });
    }
  }, [defaultIds]);

  const selectOnly = useCallback((id: string) => {
    if (!eligibleRank.has(id)) return;
    setSelectedIds([id]);
  }, [eligibleRank]);

  const toggleSelection = useCallback((id: string) => {
    if (selectionMode === 'single') {
      selectOnly(id);
      return;
    }
    if (activeIdSet.has(id)) {
      if (activeIds.length === 1) return;
      setSelectedIds((current) => {
        const source = current.length > 0 ? current : defaultIds;
        return source.filter((currentId) => currentId !== id);
      });
      return;
    }

    setSelectedIds((current) => {
      const source = current.length > 0 ? current : defaultIds;
      return [...source, id]
        .sort((a, b) => (eligibleRank.get(a) ?? 0) - (eligibleRank.get(b) ?? 0));
    });
  }, [activeIdSet, activeIds.length, defaultIds, eligibleRank, selectOnly, selectionMode]);

  const resetSelection = useCallback(() => {
    setSelectedIds(defaultIds.slice(0, 1));
  }, [defaultIds]);

  return {
    metric: viewState.metric,
    setMetric,
    availableMetrics,
    dateWindow,
    setDateWindow,
    activeDates,
    eligibleIds,
    defaultIds,
    activeIds,
    activeIdSet,
    visibleIds,
    query,
    setQuery,
    selectionMode,
    setSelectionMode,
    toggleSelection,
    selectOnly,
    resetSelection,
  };
}
