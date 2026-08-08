import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import {
  INITIAL_CURVE_VIEW_STATE,
  MAX_ACTIVE_SERIES,
  collectDates,
  epidemicCurveViewReducer,
  reconcileDateWindow,
  supportsWeeklyEquivalent,
  type CurveSeries,
  type DateWindow,
  type EpidemicMetric,
} from './epidemicCurveModel';

interface Options {
  series: Record<string, CurveSeries>;
  entityIds?: string[];
  topN: number;
  caseOnlyEntityIds?: string[];
}

export function useEpidemicCurveState({
  series,
  entityIds,
  topN,
  caseOnlyEntityIds = [],
}: Options) {
  const [viewState, dispatchView] = useReducer(
    epidemicCurveViewReducer,
    INITIAL_CURVE_VIEW_STATE
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [selectionLimitHit, setSelectionLimitHit] = useState(false);

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
    () => eligibleIds.slice(0, Math.max(1, Math.min(topN, MAX_ACTIVE_SERIES))),
    [eligibleIds, topN]
  );

  useEffect(() => {
    setSelectedIds((current) => {
      const retained = current
        .filter((id) => eligibleRank.has(id))
        .slice(0, MAX_ACTIVE_SERIES);
      return retained.length > 0 ? retained : defaultIds;
    });
  }, [defaultIds, eligibleRank]);

  const activeIds = useMemo(() => {
    const source = selectedIds.length > 0 ? selectedIds : defaultIds;
    return source
      .filter((id) => eligibleRank.has(id))
      .slice(0, MAX_ACTIVE_SERIES)
      .sort((a, b) => (eligibleRank.get(a) ?? 0) - (eligibleRank.get(b) ?? 0));
  }, [defaultIds, eligibleRank, selectedIds]);
  const activeIdSet = useMemo(() => new Set(activeIds), [activeIds]);
  const caseOnlyEntityIdSet = useMemo(
    () => new Set(caseOnlyEntityIds),
    [caseOnlyEntityIds]
  );
  const activeDates = useMemo(
    () => collectDates(series, activeIds),
    [activeIds, series]
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
    if (activeIds.some((id) => caseOnlyEntityIdSet.has(id))) return metrics;
    const hasCompatibleWeeklyValues = activeIds.length > 0 && activeIds.every((id) => (
      supportsWeeklyEquivalent(series[id])
    ));
    if (hasCompatibleWeeklyValues) metrics.push('weekly_equiv_cases');
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

  const toggleSelection = useCallback((id: string) => {
    if (activeIdSet.has(id)) {
      if (activeIds.length === 1) return;
      setSelectionLimitHit(false);
      setSelectedIds((current) => {
        const source = current.length > 0 ? current : defaultIds;
        return source.filter((currentId) => currentId !== id);
      });
      return;
    }

    if (activeIds.length >= MAX_ACTIVE_SERIES) {
      setSelectionLimitHit(true);
      return;
    }

    setSelectionLimitHit(false);
    setSelectedIds((current) => {
      const source = current.length > 0 ? current : defaultIds;
      return [...source, id]
        .sort((a, b) => (eligibleRank.get(a) ?? 0) - (eligibleRank.get(b) ?? 0));
    });
  }, [activeIdSet, activeIds.length, defaultIds, eligibleRank]);

  const resetSelection = useCallback(() => {
    setSelectionLimitHit(false);
    setSelectedIds(defaultIds);
  }, [defaultIds]);

  const selectVisible = useCallback(() => {
    if (visibleIds.length === 0) return;
    setSelectionLimitHit(visibleIds.length > MAX_ACTIVE_SERIES);
    setSelectedIds(visibleIds.slice(0, MAX_ACTIVE_SERIES));
  }, [visibleIds]);

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
    selectionLimitHit,
    toggleSelection,
    resetSelection,
    selectVisible,
  };
}
