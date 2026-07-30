export interface MonthlyData {
  months: string[];
  cases: number[];
  deaths: number[];
}

export type MonthlyMetric = 'cases' | 'deaths';

export interface YearSummary {
  year: string;
  values: number[];
  total: number;
  peakMonth: string;
  peakValue: number;
  color: string;
}

export const MONTH_NAMES = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

export const YEAR_COLORS = [
  '#0072b2',
  '#d55e00',
  '#009e73',
  '#cc79a7',
  '#e69f00',
  '#56b4e9',
  '#7f3c8d',
  '#666666',
  '#bc5090',
  '#2f4b7c',
];

export function collectYears(months: string[]) {
  return Array.from(
    new Set(months.map((month) => month.split('-')[0]).filter(Boolean))
  ).sort((left, right) => Number(left) - Number(right));
}

export function getRecentYears(years: string[], count = 5) {
  return years.slice(-Math.max(1, count));
}

export function buildYearSummaries(
  data: MonthlyData,
  metric: MonthlyMetric
): YearSummary[] {
  const years = collectYears(data.months);
  const totalsByYear = new Map(
    years.map((year) => [year, new Array<number>(12).fill(0)])
  );
  const metricValues = metric === 'cases' ? data.cases : data.deaths;

  data.months.forEach((yearMonth, index) => {
    const [year, month] = yearMonth.split('-');
    const monthIndex = Number(month) - 1;
    const values = totalsByYear.get(year);
    if (!values || monthIndex < 0 || monthIndex >= 12) return;
    const value = Number(metricValues[index]);
    if (Number.isFinite(value)) values[monthIndex] += value;
  });

  return years.map((year, index) => {
    const values = totalsByYear.get(year) ?? new Array<number>(12).fill(0);
    const total = values.reduce((sum, value) => sum + value, 0);
    const peakValue = Math.max(0, ...values);
    const peakMonthIndex = values.findIndex((value) => value === peakValue);

    return {
      year,
      values,
      total,
      peakMonth: peakValue > 0 ? (MONTH_NAMES[peakMonthIndex] ?? '—') : '—',
      peakValue,
      color: YEAR_COLORS[index % YEAR_COLORS.length],
    };
  });
}
