export interface MonthlyData {
  months: string[];
  cases: (number | null)[];
  deaths: (number | null)[];
  coverage?: {
    jurisdiction_universe_size: number;
    excluded_weekly_jurisdictions?: number;
    excluded_unknown_jurisdictions?: number;
    inferred_monthly_jurisdictions?: number;
    cases_reported_jurisdictions: number[];
    deaths_reported_jurisdictions: number[];
    cases_incompatible_months?: boolean[];
    deaths_incompatible_months?: boolean[];
  };
}

export type MonthlyMetric = 'cases' | 'deaths';

export interface YearSummary {
  year: string;
  values: (number | null)[];
  total: number | null;
  monthlyCoverage: Array<{ reported: number; eligible: number; status: string } | null>;
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
  const valuesByYear = new Map(
    years.map((year) => [year, new Array<number | null>(12).fill(null)]),
  );
  const coverageByYear = new Map(
    years.map((year) => [
      year,
      new Array<YearSummary['monthlyCoverage'][number]>(12).fill(null),
    ]),
  );
  const metricValues = metric === 'cases' ? data.cases : data.deaths;

  data.months.forEach((yearMonth, index) => {
    const [year, month] = yearMonth.split('-');
    const monthIndex = Number(month) - 1;
    const values = valuesByYear.get(year);
    if (!values || monthIndex < 0 || monthIndex >= 12) return;
    const rawValue = metricValues[index];
    if (rawValue != null && Number.isFinite(rawValue)) {
      values[monthIndex] = (values[monthIndex] ?? 0) + rawValue;
    }
    if (data.coverage) {
      const reportedCounts = metric === 'cases'
        ? data.coverage.cases_reported_jurisdictions
        : data.coverage.deaths_reported_jurisdictions;
      const reported = reportedCounts[index] ?? 0;
      const incompatible = (metric === 'cases'
        ? data.coverage.cases_incompatible_months
        : data.coverage.deaths_incompatible_months)?.[index] ?? false;
      const status = incompatible
        ? 'incompatible'
        : reported === 0
        ? 'no_data'
        : data.coverage.jurisdiction_universe_size > 0 &&
            reported === data.coverage.jurisdiction_universe_size
          ? 'complete'
          : 'partial';
      coverageByYear.get(year)![monthIndex] = {
        reported,
        eligible: data.coverage.jurisdiction_universe_size,
        status,
      };
    }
  });

  return years.map((year, index) => {
    const values = valuesByYear.get(year) ?? new Array<number | null>(12).fill(null);
    const monthlyCoverage = coverageByYear.get(year) ??
      new Array<YearSummary['monthlyCoverage'][number]>(12).fill(null);
    const coverageComplete = monthlyCoverage.every((item) => item === null || item.status === 'complete');
    const total = values.every((value) => value !== null) && coverageComplete
      ? values.reduce<number>((sum, value) => sum + (value ?? 0), 0)
      : null;
    const observed = values.filter((value): value is number => value !== null);
    const peakValue = Math.max(0, ...observed);
    const peakMonthIndex = values.findIndex((value) => value !== null && value === peakValue);

    return {
      year,
      values,
      total,
      monthlyCoverage,
      peakMonth: peakValue > 0 ? (MONTH_NAMES[peakMonthIndex] ?? '—') : '—',
      peakValue,
      color: YEAR_COLORS[index % YEAR_COLORS.length],
    };
  });
}
