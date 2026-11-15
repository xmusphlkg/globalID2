import type { CountryDatasetSeriesEntry } from '../countryDataset';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface DiseaseSection {
  section_type: string;
  title: string;
  content: string;
  content_html?: string;
  title_i18n?: Partial<Record<'zh' | 'en' | 'fr', string>>;
  content_i18n?: Partial<Record<'zh' | 'en' | 'fr', string>>;
  content_html_i18n?: Partial<Record<'zh' | 'en' | 'fr', string>>;
}

export interface DiseaseMeta {
  disease_id: string;
  name_en: string;
  name_zh: string;
  name_fr?: string;
  category: string;
  slug: string;
}

export type DiseaseSeries = Pick<CountryDatasetSeriesEntry, 'dates' | 'cases' | 'deaths' | 'incidence_rates' | 'total_cases'>
  & Partial<CountryDatasetSeriesEntry>
  & { snapshot_version?: string };

export interface ReportMeta {
  id: number;
  title: string;
  period_start: string;
  period_end: string;
  country_name: string;
  country_code: string;
  snapshot_version?: string | null;
}
