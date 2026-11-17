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
  onboarding_track?: 'National source' | 'Regional baseline' | 'International baseline' | 'Source research';
  publication_pending?: boolean;
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

  // South Asia onboarding candidates. These sources are registered in the
  // latest ingestion catalogue but remain planned until a reviewed public
  // snapshot is available.
  { code: 'IN', name_en: 'India', name_zh: '印度', lat: 22.6, lng: 79.0, status: 'Scheduled', labelOffset: [-100, 35], cadence: 'Weekly', onboarding_track: 'National source', source_name: 'India NCDC IDSP Weekly Outbreak Reports', source_url: 'https://ncdc.mohfw.gov.in/includes/WeeklyOutbreaks.php' },
  { code: 'PK', name_en: 'Pakistan', name_zh: '巴基斯坦', lat: 30.4, lng: 69.3, status: 'Scheduled', labelOffset: [-120, -30], cadence: 'Weekly', onboarding_track: 'National source', source_name: 'Pakistan NIH IDSR Weekly Bulletin', source_url: 'https://nih.org.pk/public/phb/weekly-bulletin' },
  { code: 'BD', name_en: 'Bangladesh', name_zh: '孟加拉国', lat: 23.7, lng: 90.4, status: 'Scheduled', labelOffset: [110, 35], cadence: 'Daily', onboarding_track: 'National source', source_name: 'Bangladesh DGHS Daily Dengue Hospital Surveillance', source_url: 'https://dghs.gov.bd/pages/miscellaneous-infos' },
  { code: 'NP', name_en: 'Nepal', name_zh: '尼泊尔', lat: 28.4, lng: 84.1, status: 'Scheduled', labelOffset: [80, -45], cadence: 'Weekly', onboarding_track: 'National source', source_name: 'Nepal EDCD SORMAS Public Weekly Bulletin', source_url: 'https://analysis.edcd.gov.np/' },
  { code: 'LK', name_en: 'Sri Lanka', name_zh: '斯里兰卡', lat: 7.9, lng: 80.7, status: 'Scheduled', labelOffset: [95, 40], cadence: 'Weekly', onboarding_track: 'National source', source_name: 'Sri Lanka Epidemiology Unit Weekly Epidemiological Report', source_url: 'https://www.epid.gov.lk/epid/public/weekly-epidemiological-report' },
  { code: 'AF', name_en: 'Afghanistan', name_zh: '阿富汗', lat: 33.9, lng: 67.7, status: 'Scheduled', labelOffset: [-120, -30], cadence: 'Weekly', onboarding_track: 'National source', source_name: 'Afghanistan MoPH NDSR Weekly Epidemiological Report', source_url: 'https://moph.gov.af/en/reports' },
  { code: 'BT', name_en: 'Bhutan', name_zh: '不丹', lat: 27.5, lng: 90.4, status: 'Scheduled', labelOffset: [95, -30], cadence: 'Mixed', onboarding_track: 'National source', source_name: 'Bhutan RCDC Publications and Sentinel Surveillance', source_url: 'https://www.rcdc.gov.bt/web/' },
  { code: 'MV', name_en: 'Maldives', name_zh: '马尔代夫', lat: 3.2, lng: 73.2, status: 'Scheduled', labelOffset: [95, 30], cadence: 'Monthly', onboarding_track: 'National source', source_name: 'Maldives HPA Communicable Disease Reports', source_url: 'https://health.gov.mv/en/publications?search=monthly' },

  // Existing roadmap entry retained until an official machine-readable source
  // and ingestion contract are selected.
  {
    code: 'TH', name_en: 'Thailand', name_zh: '泰国', lat: 15.5, lng: 100.5,
    status: 'Scheduled', labelOffset: [80, -30], cadence: 'To be confirmed', onboarding_track: 'Source research',
    source_name: 'DOE DDS — national export and reuse contract pending',
    source_url: 'https://ddsdoe.ddc.moph.go.th/ddss/',
  },
  // Africa, Southeast Asia and Latin America source catalogue (2026-11-16).
  // Source ingestion and public snapshots have independent completion states.
  // Keep unpublished datasets Scheduled until a real public snapshot exists.
  { code: "ZA", name_en: "South Africa", name_zh: "南非", lat: -30.6, lng: 22.9, status: 'Scheduled', labelOffset: [80, -30], cadence: "Monthly", onboarding_track: "National source", source_name: "NICD NMC Monthly Surveillance Report", source_url: "https://www.nicd.ac.za/nmc-overview/nmc-monthly-surveillance-report/" },
  { code: "NG", name_en: "Nigeria", name_zh: "尼日利亚", lat: 9.1, lng: 8.7, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "NCDC Weekly Epidemiological Reports and Situation Reports", source_url: "https://ncdc.gov.ng/reports/weekly" },
  { code: "ET", name_en: "Ethiopia", name_zh: "埃塞俄比亚", lat: 9.1, lng: 40.5, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "KE", name_en: "Kenya", name_zh: "肯尼亚", lat: 0.02, lng: 37.9, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "UG", name_en: "Uganda", name_zh: "乌干达", lat: 1.4, lng: 32.3, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "GH", name_en: "Ghana", name_zh: "加纳", lat: 7.9, lng: -1.0, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "MW", name_en: "Malawi", name_zh: "马拉维", lat: -13.3, lng: 34.3, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "PHIM IDSR weekly bulletins", source_url: "https://phim.health.gov.mw/?s=IDSR" },
  { code: "KH", name_en: "Cambodia", name_zh: "柬埔寨", lat: 12.6, lng: 105.0, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "Ministry of Health CamEWARN", source_url: "https://cdcmoh.gov.kh/surveillance/camewarn" },
  { code: "ID", name_en: "Indonesia", name_zh: "印度尼西亚", lat: -0.8, lng: 113.9, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", source_name: "Ministry of Health Indonesia Health Profile", source_url: "https://kemkes.go.id/id/profil-kesehatan-indonesia-2024" },
  { code: "PH", name_en: "Philippines", name_zh: "菲律宾", lat: 12.9, lng: 121.8, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "VN", name_en: "Viet Nam", name_zh: "越南", lat: 14.1, lng: 108.3, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "LA", name_en: "Lao PDR", name_zh: "老挝", lat: 19.9, lng: 102.5, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "MY", name_en: "Malaysia", name_zh: "马来西亚", lat: 4.2, lng: 102.0, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", source_name: "MOH annual STD notifications; broader disease coverage planned", source_url: "https://data.gov.my/data-catalogue/std_state" },
  { code: "MX", name_en: "Mexico", name_zh: "墨西哥", lat: 23.6, lng: -102.6, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", source_name: "DGE Anuario de Morbilidad", source_url: "https://epidemiologia.salud.gob.mx/anuario/html/index.html" },
  { code: "AR", name_en: "Argentina", name_zh: "阿根廷", lat: -38.4, lng: -63.6, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "Ministerio de Salud Boletín Epidemiológico Nacional", source_url: "https://www.argentina.gob.ar/salud/boletin-epidemiologico-nacional" },
  { code: "PE", name_en: "Peru", name_zh: "秘鲁", lat: -9.2, lng: -75.0, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "CDC Perú Boletines Epidemiológicos", source_url: "https://www.dge.gob.pe/portalnuevo/informacion-publica/boletines-epidemiologicos/" },
  { code: "CO", name_en: "Colombia", name_zh: "哥伦比亚", lat: 4.6, lng: -74.3, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "INS SIVIGILA historical municipality records", source_url: "https://www.datos.gov.co/d/4hyg-wa9d" },
  { code: "CL", name_en: "Chile", name_zh: "智利", lat: -35.7, lng: -71.5, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", source_name: "MINSAL ENO historical notifications", source_url: "https://datos.gob.cl/dataset/notificaciones-eno" },
  { code: "PY", name_en: "Paraguay", name_zh: "巴拉圭", lat: -23.4, lng: -58.4, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "EC", name_en: "Ecuador", name_zh: "厄瓜多尔", lat: -1.8, lng: -78.2, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "US-PR", name_en: "Puerto Rico", name_zh: "波多黎各", lat: 18.2, lng: -66.6, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "National source", source_name: "US CDC NNDSS territory observations", source_url: "https://data.cdc.gov/browse?category=NNDSS" },
  { code: "US-VI", name_en: "U.S. Virgin Islands", name_zh: "美属维尔京群岛", lat: 18.3, lng: -64.9, status: 'Scheduled', labelOffset: [80, -30], cadence: "Weekly", onboarding_track: "Source research", source_name: "US CDC NNDSS territory observations", source_url: "https://data.cdc.gov/browse?category=NNDSS" },
  { code: "BQ", name_en: "Caribbean Netherlands", name_zh: "荷属加勒比地区", lat: 12.2, lng: -68.3, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", source_name: "RIVM Caribbean Netherlands notifications", source_url: "https://www.rivm.nl/meldingsplicht-infectieziekten/overzicht-meldingen" },
  { code: "GP", name_en: "Guadeloupe", name_zh: "瓜德罗普", lat: 16.3, lng: -61.6, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", publication_pending: true, source_name: "Santé publique France ODISSÉ annual notifications", source_url: "https://odisse.santepubliquefrance.fr/" },
  { code: "MQ", name_en: "Martinique", name_zh: "马提尼克", lat: 14.6, lng: -61.0, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", publication_pending: true, source_name: "Santé publique France ODISSÉ annual notifications", source_url: "https://odisse.santepubliquefrance.fr/" },
  { code: "GF", name_en: "French Guiana", name_zh: "法属圭亚那", lat: 3.9, lng: -53.1, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "National source", publication_pending: true, source_name: "Santé publique France ODISSÉ annual notifications", source_url: "https://odisse.santepubliquefrance.fr/" },
  { code: "TZ", name_en: "Tanzania", name_zh: "坦桑尼亚", lat: -6.4, lng: 34.9, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "MM", name_en: "Myanmar", name_zh: "缅甸", lat: 21.9, lng: 96, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "BN", name_en: "Brunei Darussalam", name_zh: "文莱", lat: 4.5, lng: 114.7, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "TL", name_en: "Timor-Leste", name_zh: "东帝汶", lat: -8.9, lng: 125.7, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "CR", name_en: "Costa Rica", name_zh: "哥斯达黎加", lat: 9.7, lng: -83.8, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
  { code: "PA", name_en: "Panama", name_zh: "巴拿马", lat: 8.5, lng: -80.8, status: 'Scheduled', labelOffset: [80, -30], cadence: "Annual", onboarding_track: "International baseline", publication_pending: true, source_name: "WHO GHO country-reported annual infections", source_url: "https://www.who.int/data/gho" },
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
  lang: 'en' | 'zh' | 'fr',
  fallbackName?: string,
) {
  return lang === 'zh'
    ? country.name_zh || fallbackName || country.name_en
    : fallbackName || country.name_en;
}

export function getCoverageLabelOffset(code: string): [number, number] {
  return getCountryCoverage(code)?.labelOffset ?? [80, -30];
}
