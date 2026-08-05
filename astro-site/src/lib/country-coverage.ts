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

export const COUNTRY_COVERAGE: CountryCoverageItem[] = [
  // Active country pipelines. Database metadata remains the source of truth for
  // their totals, disease counts, coverage windows, and supported status.
  { code: 'AU', name_en: 'Australia', name_zh: '澳大利亚', lat: -25, lng: 133, status: 'Supported', labelOffset: [92, -38] },
  { code: 'BR', name_en: 'Brazil', name_zh: '巴西', lat: -14.2, lng: -51.9, status: 'Supported', labelOffset: [112, 34] },
  { code: 'CH', name_en: 'Switzerland', name_zh: '瑞士', lat: 46.8, lng: 8.2, status: 'Supported', labelOffset: [-76, 10] },
  { code: 'CN', name_en: 'China', name_zh: '中国', lat: 35, lng: 104, status: 'Supported', labelOffset: [-82, -8] },
  { code: 'HK', name_en: 'Hong Kong, China', name_zh: '中国香港', lat: 22.32, lng: 114.17, status: 'Supported', labelOffset: [-120, 10] },
  { code: 'JP', name_en: 'Japan', name_zh: '日本', lat: 36, lng: 138, status: 'Supported', labelOffset: [112, -18] },
  { code: 'KR', name_en: 'South Korea', name_zh: '韩国', lat: 36.5, lng: 127.5, status: 'Supported', labelOffset: [-92, -86] },
  { code: 'NZ', name_en: 'New Zealand', name_zh: '新西兰', lat: -41, lng: 171, status: 'Supported', labelOffset: [94, -18] },
  { code: 'TW', name_en: 'Taiwan, China', name_zh: '中国台湾', lat: 23.7, lng: 121, status: 'Supported', labelOffset: [118, 28] },
  { code: 'US', name_en: 'United States', name_zh: '美国', lat: 39, lng: -98, status: 'Supported', labelOffset: [-120, -28] },

  // National-source onboarding candidates, ordered by recommended delivery
  // sequence. These entries are visible on the map and the country cards, but
  // remain Scheduled until a real database snapshot exists.
  {
    code: 'SG', name_en: 'Singapore', name_zh: '新加坡', lat: 1.35, lng: 103.8,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Weekly', onboarding_track: 'National source',
    source_name: 'CDA Weekly Infectious Diseases Bulletin',
    source_url: 'https://www.cda.gov.sg/resources/weekly-infectious-diseases-bulletin-2026/',
  },
  {
    code: 'AT', name_en: 'Austria', name_zh: '奥地利', lat: 47.5, lng: 14.5,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Monthly', onboarding_track: 'National source',
    source_name: 'AGES Radar for Infectious Diseases',
    source_url: 'https://www.ages.at/en/human/disease/ages-radar-for-infectious-diseases/',
  },
  {
    code: 'IE', name_en: 'Ireland', name_zh: '爱尔兰', lat: 53.3, lng: -8,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Weekly', onboarding_track: 'National source',
    source_name: 'HPSC National Notifiable Disease Hub',
    source_url: 'https://notifiabledisease.hpsc.ie/',
  },
  {
    code: 'SE', name_en: 'Sweden', name_zh: '瑞典', lat: 62, lng: 18,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Monthly', onboarding_track: 'National source',
    source_name: 'Public Health Agency of Sweden',
    source_url: 'https://www.folkhalsomyndigheten.se/statistik-och-data/hitta-statistik-och-data/smittsamma-sjukdomar-statistik/',
  },
  {
    code: 'NO', name_en: 'Norway', name_zh: '挪威', lat: 62, lng: 10,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Daily', onboarding_track: 'National source',
    source_name: 'FHI MSIS Statistics Bank',
    source_url: 'https://allvis.fhi.no/msis',
  },
  {
    code: 'DK', name_en: 'Denmark', name_zh: '丹麦', lat: 56, lng: 10,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Weekdays', onboarding_track: 'National source',
    source_name: 'SSI Surveillance Statistics',
    source_url: 'https://statistik.ssi.dk/',
  },
  {
    code: 'FI', name_en: 'Finland', name_zh: '芬兰', lat: 64, lng: 26,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Periodic', onboarding_track: 'National source',
    source_name: 'THL Infectious Diseases Register',
    source_url: 'https://sampo.thl.fi/pivot/prod/en/ttr/cases/fact_ttr_cases',
  },
  {
    code: 'CA', name_en: 'Canada', name_zh: '加拿大', lat: 56, lng: -106,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'National source',
    source_name: 'Canadian Notifiable Disease Surveillance System',
    source_url: 'https://diseases.canada.ca/notifiable/charts-list?wbdisable=true',
  },
  {
    code: 'DE', name_en: 'Germany', name_zh: '德国', lat: 51, lng: 10,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Current aggregate', onboarding_track: 'National source',
    source_name: 'RKI SurvStat',
    source_url: 'https://survstat.rki.de/',
  },
  {
    code: 'GB', name_en: 'England & Wales', name_zh: '英格兰与威尔士', lat: 52.5, lng: -2.5,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Weekly', onboarding_track: 'National source',
    source_name: 'UKHSA Notifiable Causative Agents Reports',
    source_url: 'https://www.gov.uk/government/publications/notifiable-diseases-causative-agents-reports-for-2026',
  },
  {
    code: 'NL', name_en: 'Netherlands', name_zh: '荷兰', lat: 52.2, lng: 5.3,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'National source',
    source_name: 'RIVM Infectious Disease Notifications',
    source_url: 'https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen',
  },
  {
    code: 'IS', name_en: 'Iceland', name_zh: '冰岛', lat: 65, lng: -19,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'Mixed', onboarding_track: 'National source',
    source_name: 'Directorate of Health Infectious Disease Statistics',
    source_url: 'https://island.is/en/smitsjukdomar-tolur',
  },

  // Countries that can receive a comparable historical baseline from the ECDC
  // Surveillance Atlas before a higher-frequency national source is onboarded.
  { code: 'FR', name_en: 'France', name_zh: '法国', lat: 46.2, lng: 2.2, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'ES', name_en: 'Spain', name_zh: '西班牙', lat: 40.4, lng: -3.7, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'IT', name_en: 'Italy', name_zh: '意大利', lat: 42.8, lng: 12.8, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'PT', name_en: 'Portugal', name_zh: '葡萄牙', lat: 39.5, lng: -8, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'PL', name_en: 'Poland', name_zh: '波兰', lat: 52, lng: 19, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'CZ', name_en: 'Czechia', name_zh: '捷克', lat: 49.8, lng: 15.5, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'GR', name_en: 'Greece', name_zh: '希腊', lat: 39, lng: 22, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },
  { code: 'RO', name_en: 'Romania', name_zh: '罗马尼亚', lat: 46, lng: 25, status: 'Scheduled', labelOffset: [80, -30], cadence: 'Annual', onboarding_track: 'Regional baseline', source_name: 'ECDC Surveillance Atlas', source_url: 'https://atlas.ecdc.europa.eu/public/index.aspx/' },

  // Existing roadmap entry retained until an official machine-readable source
  // and ingestion contract are selected.
  {
    code: 'TH', name_en: 'Thailand', name_zh: '泰国', lat: 15.5, lng: 100.5,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'To be confirmed', onboarding_track: 'Source research',
  },
];

const COUNTRY_COVERAGE_BY_CODE = new Map(
  COUNTRY_COVERAGE.map((country) => [country.code, country]),
);

export function getCountryCoverage(code: string | null | undefined) {
  return COUNTRY_COVERAGE_BY_CODE.get((code ?? '').toUpperCase());
}

export function resolveCoverageStatus(country: Pick<CountryCoverageItem, 'status'>, hasMeta = false): CoverageStatus {
  return hasMeta || country.status === 'Supported' ? 'Supported' : 'Scheduled';
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
