import { type CountryDatasetSeriesEntry } from '../countryDataset';

export type Lang = 'zh' | 'en' | 'fr';

export type AnyRecord = Record<string, any>;

export type SparklineSeriesEntry = Pick<CountryDatasetSeriesEntry, 'cases' | 'weekly_equiv_cases'> & {
  dates?: string[];
  monthly_cases?: number[];
  annual_cases?: number[];
  current_year?: string | null;
  current_year_cumulative_cases?: number | null;
};
