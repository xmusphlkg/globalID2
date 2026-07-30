import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  buildYearSummaries,
  collectYears,
  getRecentYears,
  type MonthlyData,
  type MonthlyMetric,
} from './monthlyBarModel';

export function useMonthlyBarState(data: MonthlyData) {
  const allYears = useMemo(() => collectYears(data.months), [data.months]);
  const recentYears = useMemo(() => getRecentYears(allYears, 5), [allYears]);
  const [metric, setMetric] = useState<MonthlyMetric>('cases');
  const [selectedYears, setSelectedYears] = useState<string[]>(() => recentYears);
  const [query, setQuery] = useState('');

  useEffect(() => {
    setSelectedYears((current) => {
      const retained = allYears.filter((year) => current.includes(year));
      return retained.length > 0 ? retained : recentYears;
    });
  }, [allYears, recentYears]);

  const hasDeathsMetric = useMemo(
    () => data.deaths.some((value) => (value ?? 0) > 0),
    [data.deaths]
  );
  useEffect(() => {
    if (!hasDeathsMetric && metric === 'deaths') setMetric('cases');
  }, [hasDeathsMetric, metric]);

  const yearSummaries = useMemo(
    () => buildYearSummaries(data, metric),
    [data, metric]
  );
  const selectedYearSet = useMemo(
    () => new Set(selectedYears),
    [selectedYears]
  );
  const activeYearSummaries = useMemo(
    () => yearSummaries.filter((summary) => selectedYearSet.has(summary.year)),
    [selectedYearSet, yearSummaries]
  );
  const visibleYearSummaries = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const newestFirst = [...yearSummaries].reverse();
    return normalizedQuery
      ? newestFirst.filter((summary) => summary.year.toLowerCase().includes(normalizedQuery))
      : newestFirst;
  }, [query, yearSummaries]);

  const toggleYear = useCallback((year: string) => {
    setSelectedYears((current) => {
      if (current.includes(year)) {
        if (current.length === 1) return current;
        return current.filter((item) => item !== year);
      }
      return allYears.filter((item) => item === year || current.includes(item));
    });
  }, [allYears]);

  const selectRecentYears = useCallback(() => {
    setSelectedYears(recentYears);
  }, [recentYears]);

  const selectAllYears = useCallback(() => {
    setSelectedYears(allYears);
  }, [allYears]);

  const selectVisibleYears = useCallback(() => {
    const visibleYears = new Set(
      visibleYearSummaries.map((summary) => summary.year)
    );
    const nextYears = allYears.filter((year) => visibleYears.has(year));
    if (nextYears.length > 0) setSelectedYears(nextYears);
  }, [allYears, visibleYearSummaries]);

  const isShowingAllYears = selectedYears.length === allYears.length;
  const isShowingRecentYears = selectedYears.length === recentYears.length
    && recentYears.every((year) => selectedYearSet.has(year));

  return {
    metric,
    setMetric,
    hasDeathsMetric,
    allYears,
    recentYears,
    yearSummaries,
    activeYearSummaries,
    visibleYearSummaries,
    selectedYears,
    selectedYearSet,
    query,
    setQuery,
    toggleYear,
    selectRecentYears,
    selectAllYears,
    selectVisibleYears,
    isShowingAllYears,
    isShowingRecentYears,
  };
}
