import type { Summary } from './surveillance-presentation.ts';

export function latestSnapshot(row: Summary) {
  const dates = row.dates_tail ?? [], values = row.cases_tail ?? [];
  let index = values.length - 1;
  while (index >= 0 && !(typeof values[index] === 'number' && Number.isFinite(values[index]) && dates[index])) index--;
  if (index < 0) return null;
  const previous = values[index - 1];
  // Only adjacent periods with a declared common cadence are compared.
  const cadenceDays: Record<string, number> = { daily: 1, weekly: 7 };
  const months: Record<string, number> = { monthly: 1, quarterly: 3, annual: 12, yearly: 12 };
  const currentDate = new Date(`${dates[index]}T00:00:00Z`), previousDate = new Date(`${dates[index - 1]}T00:00:00Z`);
  const unit = String(row.period_granularity ?? '').toLowerCase();
  const adjacent = cadenceDays[unit] ? (currentDate.getTime() - previousDate.getTime()) / 86400000 === cadenceDays[unit]
    : months[unit] ? (currentDate.getUTCFullYear() - previousDate.getUTCFullYear()) * 12 + currentDate.getUTCMonth() - previousDate.getUTCMonth() === months[unit] : false;
  const comparable = adjacent && row.comparability !== 'not_comparable' && row.data_layer !== 'mixed';
  return { date: dates[index], value: values[index], change: comparable && typeof previous === 'number' && previous > 0 ? (values[index] - previous) / previous * 100 : null };
}
