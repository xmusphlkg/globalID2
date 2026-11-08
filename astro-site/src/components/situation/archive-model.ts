import type { WeeklyChange } from '../../utils/situationTypes.ts';

export function monthOfWeek(period: string) {
  const [year, week] = period.split('-W').map(Number);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  jan4.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (week - 1) * 7);
  return jan4.toISOString().slice(0, 7);
}
export function weekOfDay(day: string) {
  const when = new Date(`${String(day).slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(when.getTime())) return '';
  when.setUTCDate(when.getUTCDate() - ((when.getUTCDay() + 6) % 7) + 3);
  const year = when.getUTCFullYear(), jan4 = new Date(Date.UTC(year, 0, 4));
  const week = 1 + Math.round((((when.getTime() - jan4.getTime()) / 86_400_000) - 3 + ((jan4.getUTCDay() + 6) % 7)) / 7);
  return `${year}-W${String(week).padStart(2, '0')}`;
}
export function monthTotals(weeks: WeeklyChange[]): WeeklyChange {
  return { period_key: '', new_outbreaks: [...new Set(weeks.flatMap(row => row.new_outbreaks))], updated_outbreaks: [...new Set(weeks.flatMap(row => row.updated_outbreaks))], new_places: weeks.reduce((sum, row) => sum + row.new_places, 0), alerts: weeks.reduce((sum, row) => sum + row.alerts, 0) };
}
