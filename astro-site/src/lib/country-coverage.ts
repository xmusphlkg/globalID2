export type CoverageStatus = 'Supported' | 'Scheduled';

export interface CountryCoverageItem {
  code: string;
  name_en: string;
  name_zh: string;
  lat: number;
  lng: number;
  status: CoverageStatus;
  labelOffset: [number, number];
  source_name?: string;
  source_url?: string;
  cadence?: string;
  onboarding_track?: 'National source' | 'Regional baseline' | 'Source research';
}

export interface CountryDataSnapshotMeta {
  data_available?: boolean;
  record_count?: number;
  disease_count?: number;
  date_range?: { start?: string | null; end?: string | null } | null;
}

export const COUNTRY_COVERAGE: CountryCoverageItem[] = [
  // Active country pipelines. Database metadata remains the source of truth for
  // their totals, disease counts, coverage windows, and supported status.
  {
    code: 'AU', name_en: 'Australia', name_zh: '澳大利亚', lat: -25, lng: 133,
    status: 'Supported', labelOffset: [140, -40],
  },
  {
    code: 'BR', name_en: 'Brazil', name_zh: '巴西', lat: -14.2, lng: -51.9,
    status: 'Supported', labelOffset: [-100, 0],
  },
  {
    code: 'CH', name_en: 'Switzerland', name_zh: '瑞士', lat: 46.8, lng: 8.2,
    status: 'Supported', labelOffset: [-135, 35],
  },
  {
    code: 'CN', name_en: 'China', name_zh: '中国', lat: 35, lng: 104,
    status: 'Supported', labelOffset: [-82, 58],
  },
  {
    code: 'HK', name_en: 'Hong Kong, China', name_zh: '中国香港', lat: 22.32, lng: 114.17,
    status: 'Supported', labelOffset: [142, 40],
  },
  {
    code: 'JP', name_en: 'Japan', name_zh: '日本', lat: 36, lng: 138,
    status: 'Supported', labelOffset: [90, -30],
  },
  {
    code: 'KR', name_en: 'South Korea', name_zh: '韩国', lat: 36.5, lng: 127.5,
    status: 'Supported', labelOffset: [80, -86],
  },
  {
    code: 'NZ', name_en: 'New Zealand', name_zh: '新西兰', lat: -41, lng: 171,
    status: 'Supported', labelOffset: [94, -18],
  },
  {
    code: 'TW', name_en: 'Taiwan, China', name_zh: '中国台湾', lat: 23.7, lng: 121,
    status: 'Supported', labelOffset: [128, -10],
  },
  {
    code: 'US', name_en: 'United States', name_zh: '美国', lat: 39, lng: -98,
    status: 'Supported', labelOffset: [-140, 28],
  },

  // Countries promoted from the national-source roadmap. Regional baselines
  // remain explicitly labelled where they are the currently published feed.
  {
    code: 'SG', name_en: 'Singapore', name_zh: '新加坡', lat: 1.35, lng: 103.8,
    status: 'Supported', labelOffset: [-100, 100], cadence: 'Weekly',
    source_name: 'CDA Weekly Infectious Diseases Bulletin',
    source_url: 'https://www.cda.gov.sg/resources/weekly-infectious-diseases-bulletin-2026/',
  },
  {
    code: 'AT', name_en: 'Austria', name_zh: '奥地利', lat: 47.5, lng: 14.5,
    status: 'Supported', labelOffset: [-80, -130], cadence: 'Annual', onboarding_track: 'Regional baseline',
    source_name: 'ECDC Surveillance Atlas',
    source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/',
  },
  {
    code: 'IE', name_en: 'Ireland', name_zh: '爱尔兰', lat: 53.3, lng: -8,
    status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline',
    source_name: 'ECDC Surveillance Atlas',
    source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/',
  },
  {
    code: 'SE', name_en: 'Sweden', name_zh: '瑞典', lat: 62, lng: 18,
    status: 'Supported', labelOffset: [120, -30], cadence: 'Monthly', onboarding_track: 'National source',
    source_name: 'Public Health Agency of Sweden',
    source_url: 'https://www.folkhalsomyndigheten.se/statistik-och-data/hitta-statistik-och-data/smittsamma-sjukdomar-statistik/',
  },
  {
    code: 'NO', name_en: 'Norway', name_zh: '挪威', lat: 62, lng: 10,
    status: 'Supported', labelOffset: [-80, -90], cadence: 'Daily', onboarding_track: 'National source',
    source_name: 'FHI MSIS Statistics Bank',
    source_url: 'https://allvis.fhi.no/msis',
  },
  {
    code: 'DK', name_en: 'Denmark', name_zh: '丹麦', lat: 56, lng: 10,
    status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline',
    source_name: 'ECDC Surveillance Atlas',
    source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/',
  },
  {
    code: 'FI', name_en: 'Finland', name_zh: '芬兰', lat: 64, lng: 26,
    status: 'Supported', labelOffset: [80, -80], cadence: 'Monthly', onboarding_track: 'National source',
    source_name: 'THL Infectious Diseases Register',
    source_url: 'https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases',
  },
  {
    code: 'CA', name_en: 'Canada', name_zh: '加拿大', lat: 56, lng: -106,
    status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'National source',
    source_name: 'Canadian Notifiable Disease Surveillance System',
    source_url: 'https://diseases.canada.ca/notifiable/extract-dataset',
  },
  {
    code: 'CA-ON', name_en: 'Ontario, Canada', name_zh: '加拿大安大略省', lat: 50, lng: -85,
    status: 'Scheduled', labelOffset: [-174, -30], cadence: 'Monthly',
    source_name: 'Public Health Ontario Reportable Disease Trends',
    source_url: 'https://www.publichealthontario.ca/en/Data-and-Analysis/Infectious-Disease/Reportable-Disease-Trends-Annually',
  },
  {
    code: 'DE', name_en: 'Germany', name_zh: '德国', lat: 51, lng: 10,
    status: 'Supported', labelOffset: [80, -30], cadence: 'Weekly', onboarding_track: 'National source',
    source_name: 'RKI SurvStat',
    source_url: 'https://survstat.rki.de/',
  },
  {
    code: 'GB', name_en: 'United Kingdom', name_zh: '英国', lat: 54.5, lng: -3.5,
    status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline',
    source_name: 'ECDC Surveillance Atlas historical baseline',
    source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/',
  },
  {
    code: 'NL', name_en: 'Netherlands', name_zh: '荷兰', lat: 52.2, lng: 5.3,
    status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline',
    source_name: 'ECDC Surveillance Atlas',
    source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/',
  },
  {
    code: 'IS', name_en: 'Iceland', name_zh: '冰岛', lat: 65, lng: -19,
    status: 'Supported', labelOffset: [-100, -20], cadence: 'Mixed', onboarding_track: 'National source',
    source_name: 'Directorate of Health Infectious Disease Statistics',
    source_url: 'https://island.is/en/smitsjukdomar-tolur',
  },

  // Countries that can receive a comparable historical baseline from the ECDC
  // Surveillance Atlas before a higher-frequency national source is onboarded.
  { code: 'FR', name_en: 'France', name_zh: '法国', lat: 46.2, lng: 2.2, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'ES', name_en: 'Spain', name_zh: '西班牙', lat: 40.4, lng: -3.7, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'IT', name_en: 'Italy', name_zh: '意大利', lat: 42.8, lng: 12.8, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'PT', name_en: 'Portugal', name_zh: '葡萄牙', lat: 39.5, lng: -8, status: 'Supported', labelOffset: [-95, 65], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'PL', name_en: 'Poland', name_zh: '波兰', lat: 52, lng: 19, status: 'Supported', labelOffset: [-160, 0], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'CZ', name_en: 'Czechia', name_zh: '捷克', lat: 49.8, lng: 15.5, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'GR', name_en: 'Greece', name_zh: '希腊', lat: 39, lng: 22, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'RO', name_en: 'Romania', name_zh: '罗马尼亚', lat: 46, lng: 25, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'BE', name_en: 'Belgium', name_zh: '比利时', lat: 50.8, lng: 4.5, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'BG', name_en: 'Bulgaria', name_zh: '保加利亚', lat: 42.7, lng: 25.5, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'HR', name_en: 'Croatia', name_zh: '克罗地亚', lat: 45.1, lng: 15.2, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'CY', name_en: 'Cyprus', name_zh: '塞浦路斯', lat: 35.1, lng: 33.4, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'EE', name_en: 'Estonia', name_zh: '爱沙尼亚', lat: 58.6, lng: 25, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'HU', name_en: 'Hungary', name_zh: '匈牙利', lat: 47.2, lng: 19.5, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'LV', name_en: 'Latvia', name_zh: '拉脱维亚', lat: 56.9, lng: 24.6, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'LI', name_en: 'Liechtenstein', name_zh: '列支敦士登', lat: 47.2, lng: 9.55, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'LT', name_en: 'Lithuania', name_zh: '立陶宛', lat: 55.2, lng: 23.9, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'LU', name_en: 'Luxembourg', name_zh: '卢森堡', lat: 49.8, lng: 6.1, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'MT', name_en: 'Malta', name_zh: '马耳他', lat: 35.9, lng: 14.4, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'SI', name_en: 'Slovenia', name_zh: '斯洛文尼亚', lat: 46.1, lng: 14.8, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'SK', name_en: 'Slovakia', name_zh: '斯洛伐克', lat: 48.7, lng: 19.7, status: 'Supported', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },

  // Existing roadmap entry retained until an official machine-readable source
  // and ingestion contract are selected.
  {
    code: 'TH', name_en: 'Thailand', name_zh: '泰国', lat: 15.5, lng: 100.5,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'To be confirmed', onboarding_track: 'Source research',
    source_name: 'DOE DDS — national export and reuse contract pending',
    source_url: 'https://ddsdoe.ddc.moph.go.th/ddss/',
  },
];

const COUNTRY_COVERAGE_BY_CODE = new Map(
  COUNTRY_COVERAGE.map((country) => [country.code, country]),
);

export function getCountryCoverage(code: string | null | undefined) {
  return COUNTRY_COVERAGE_BY_CODE.get((code ?? '').toUpperCase());
}

export function hasCountryDataSnapshot(
  meta: CountryDataSnapshotMeta | null | undefined,
): boolean {
  if (!meta) return false;
  if (meta.data_available === true) return true;
  if ((meta.record_count ?? 0) > 0) return true;

  const dateRange = meta.date_range;
  return (meta.disease_count ?? 0) > 0
    && Boolean(dateRange?.start || dateRange?.end);
}

export function resolveCoverageStatus(
  country: Pick<CountryCoverageItem, 'status'>,
  hasDataSnapshot = false,
): CoverageStatus {
  return hasDataSnapshot || country.status === 'Supported' ? 'Supported' : 'Scheduled';
}

export function getCoverageDisplayName(
  country: Pick<CountryCoverageItem, 'name_en' | 'name_zh'>,
  lang: 'en' | 'zh',
  fallbackName?: string,
) {
  return lang === 'zh'
    ? country.name_zh || fallbackName || country.name_en
    : fallbackName || country.name_en;
}

export function getCoverageLabelOffset(code: string): [number, number] {
  return getCountryCoverage(code)?.labelOffset ?? [80, -30];
}
