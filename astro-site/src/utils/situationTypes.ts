/** Data contract of the Situation export (`gids.situation_alerts.v1`). No Node imports: safe for the client bundle. */

export type Locale = 'en' | 'zh' | 'fr';
export type AlertStatus = 'new' | 'ongoing' | 'imported' | 'ended' | 'routine';
export type CountBasis = 'confirmed' | 'probable' | 'suspected' | 'total';

export interface AlertAI {
  review_mode: 'cross_model' | 'single_model' | null;
  input_mode: 'native_file' | 'text' | null;
  verdict: string | null;
  prompt_version: string | null;
}

export interface SituationAlertRecord {
  alert_id: string;
  outbreak_id: string | null;
  notice_id: string;
  source_id: string;
  source_label_en: string | null;
  source_label_zh: string | null;
  agency_en: string | null;
  agency_zh: string | null;
  title_en: string | null;
  url: string;
  disease_id: string | null;
  disease_slug: string | null;
  disease_label: string;
  disease_name_en: string;
  disease_name_zh: string | null;
  country_code: string;
  country_name_en: string;
  country_name_zh: string | null;
  admin_area: string | null;
  status: AlertStatus;
  cases: number | null;
  deaths: number | null;
  count_basis: CountBasis | null;
  as_of: string | null;
  published_date: string;
  /** Null where the source's redistribution terms do not allow a quote. */
  quote: string | null;
  summary_en: string | null;
  summary_zh: string | null;
  summary_fr: string | null;
  ai: AlertAI | null;
}

/** The latest local surveillance series for the point's country and disease. */
export interface LocalSeries {
  value: number;
  date: string | null;
  countryHref: string;
  curveHref: string | null;
}

/**
 * One place on the map page. Labels and dates only: the quote, summaries and
 * review metadata live on the outbreak timeline fetched when the place is opened.
 */
export interface MapMarker {
  alert_id: string;
  outbreak_id: string | null;
  source_id: string;
  source_label_en?: string | null;
  source_label_zh?: string | null;
  disease_id?: string | null;
  disease_slug?: string | null;
  disease_label: string;
  disease_name_en: string;
  disease_name_zh?: string | null;
  country_code: string;
  country_name_en: string;
  country_name_zh?: string | null;
  admin_area?: string | null;
  status: AlertStatus;
  published_date: string;
  local?: LocalSeries | null;
}

export type MapPoint = MapMarker;

export interface FacetEntry {
  id: string;
  count: number;
  [field: string]: string | number | null | undefined;
}

export interface OutbreakSummary {
  outbreak_id: string;
  disease_id: string | null;
  disease_slug: string | null;
  disease_label: string;
  disease_name_en: string;
  disease_name_zh: string | null;
  first_published: string;
  last_published: string;
  country_codes: string[];
  alert_count: number;
  notice_count: number;
  source_ids: string[];
  status: 'active' | 'quiet';
}

export interface OutbreakFile extends OutbreakSummary {
  schema_version: string;
  as_of: string;
  alerts: SituationAlertRecord[];
}

export interface SituationMapFile {
  schema_version: string;
  generated_at: string;
  as_of: string;
  since_date: string;
  point_count: number;
  points: MapMarker[];
  facets: { diseases: FacetEntry[]; countries: FacetEntry[]; sources: FacetEntry[] };
  outbreaks: OutbreakSummary[];
}

export interface WeeklyChange {
  period_key: string;
  new_outbreaks: string[];
  updated_outbreaks: string[];
  new_places: number;
  alerts: number;
}

export interface MonitoringProduct {
  notice_id: string;
  source_id: string;
  source_label_en: string | null;
  source_label_zh: string | null;
  agency_en: string | null;
  agency_zh: string | null;
  title_en: string | null;
  url: string;
  published_date: string;
  alert_count: number;
}

export interface SourceStatus {
  source_id: string;
  label?: { en?: string; zh?: string };
  agency?: { en?: string; zh?: string };
  homepage?: string;
  redistribution?: string;
  status: string | null;
  fetched_count: number | null;
  error: string | null;
}
