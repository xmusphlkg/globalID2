import { type SparklineSeriesEntry } from './types.ts';

export function numericSeries(value: unknown): number[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'number' && Number.isFinite(item)) ? value : [];
}

export function primaryCaseSeries(record: SparklineSeriesEntry | undefined): number[] {
  if (!record) return [];
  return numericSeries(record.cases);
}

export function aggregateCurveByPeriod(record: SparklineSeriesEntry | undefined, period: 'month' | 'year'): number[] {
  if (!record) return [];
  const explicit = period === 'month' ? numericSeries(record.monthly_cases) : numericSeries(record.annual_cases);
  if (explicit.length > 0) return explicit.slice(period === 'month' ? -12 : -10);

  const dates = Array.isArray(record.dates) ? record.dates : [];
  const values = primaryCaseSeries(record);
  if (dates.length === 0 || values.length === 0) {
    return period === 'month' ? values.slice(-24) : [];
  }

  const buckets = new Map<string, number>();
  dates.forEach((date, index) => {
    const key = String(date || '').slice(0, period === 'month' ? 7 : 4);
    if ((period === 'month' && key.length !== 7) || (period === 'year' && key.length !== 4)) return;
    buckets.set(key, (buckets.get(key) || 0) + Number(values[index] || 0));
  });
  return [...buckets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, value]) => value)
    .slice(period === 'month' ? -12 : -10);
}

export function currentYearCumulativeCases(record: SparklineSeriesEntry | undefined): number | null {
  if (!record) return null;
  const explicit = record.current_year_cumulative_cases == null ? NaN : Number(record.current_year_cumulative_cases);
  if (Number.isFinite(explicit)) return explicit;

  const annual = numericSeries(record.annual_cases);
  if (annual.length > 0) return annual.at(-1) ?? null;

  const dates = Array.isArray(record.dates) ? record.dates : [];
  const values = primaryCaseSeries(record);
  const latestYear = String(dates.at(-1) || '').slice(0, 4);
  if (latestYear.length !== 4 || dates.length === 0 || values.length === 0) return null;
  return dates.reduce((sum, date, index) => (
    String(date || '').startsWith(latestYear) ? sum + Number(values[index] || 0) : sum
  ), 0);
}
