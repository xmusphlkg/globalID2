/** UTC calendar helpers for the public data-updates page. */

const DAY = /^\d{4}-\d{2}-\d{2}$/;

function isCalendarDay(value: string): boolean {
  if (!DAY.test(value)) return false;
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}

/** Date-only website releases stay on their day; timestamps are grouped in UTC. */
export function utcDay(value: string): string | null {
  if (DAY.test(value)) return isCalendarDay(value) ? value : null;
  if (!isCalendarDay(value.slice(0, 10))) return null;
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toISOString().slice(0, 10) : null;
}

export function monthEnd(month: string): string {
  const year = Number(month.slice(0, 4));
  const mon = Number(month.slice(5, 7));
  const last = new Date(Date.UTC(year, mon, 0)).getUTCDate();
  return `${month}-${String(last).padStart(2, '0')}`;
}

export function monthsCovering(from: string, to: string): string[] {
  if (!isCalendarDay(from) || !isCalendarDay(to) || from > to) return [];
  const months: string[] = [];
  let year = Number(from.slice(0, 4));
  let month = Number(from.slice(5, 7));
  const end = to.slice(0, 7);
  while (`${year}-${String(month).padStart(2, '0')}` <= end && months.length < 36) {
    months.push(`${year}-${String(month).padStart(2, '0')}`);
    month += 1;
    if (month === 13) {
      month = 1;
      year += 1;
    }
  }
  return months;
}

export function daysCovering(from: string, to: string): string[] {
  if (!isCalendarDay(from) || !isCalendarDay(to) || from > to) return [];
  const days: string[] = [];
  const cursor = new Date(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  while (cursor.getTime() <= end && days.length < 800) {
    days.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return days;
}

export function rangeProblem(from: string, to: string): 'incomplete' | 'invalid' | 'reversed' | 'wide' | null {
  if (!from || !to) return 'incomplete';
  if (!isCalendarDay(from) || !isCalendarDay(to)) return 'invalid';
  if (from > to) return 'reversed';
  if (monthsCovering(from, to).length > 24) return 'wide';
  return null;
}

export function dailyCounts(days: string[], dates: string[]): Array<{ day: string; count: number }> {
  const counts = new Map(days.map(day => [day, 0]));
  for (const date of dates) {
    const day = utcDay(date);
    if (day && counts.has(day)) counts.set(day, (counts.get(day) ?? 0) + 1);
  }
  return days.map(day => ({ day, count: counts.get(day) ?? 0 }));
}
