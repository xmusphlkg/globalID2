export type CoverageStatus = 'Supported' | 'Scheduled';

export interface CountryCoverageItem {
  code: string;
  name_en: string;
  name_zh: string;
  lat: number;
  lng: number;
  status: CoverageStatus;
  labelOffset: [number, number];
}

export const COUNTRY_COVERAGE: CountryCoverageItem[] = [
  { code: 'CN', name_en: 'China', name_zh: '中国', lat: 35, lng: 104, status: 'Supported', labelOffset: [-82, 6] },
  { code: 'TH', name_en: 'Thailand', name_zh: '泰国', lat: 15.5, lng: 100.5, status: 'Scheduled', labelOffset: [-82, 52] },
  { code: 'AU', name_en: 'Australia', name_zh: '澳大利亚', lat: -25, lng: 133, status: 'Supported', labelOffset: [92, -38] },
  { code: 'BR', name_en: 'Brazil', name_zh: '巴西', lat: -14.2, lng: -51.9, status: 'Supported', labelOffset: [112, 34] },
  { code: 'US', name_en: 'United States', name_zh: '美国', lat: 39, lng: -98, status: 'Supported', labelOffset: [-120, -28] },
  { code: 'GB', name_en: 'United Kingdom', name_zh: '英国', lat: 54, lng: -3, status: 'Scheduled', labelOffset: [-112, -34] },
  { code: 'KR', name_en: 'South Korea', name_zh: '韩国', lat: 36.5, lng: 127.5, status: 'Supported', labelOffset: [-92, -86] },
  { code: 'TW', name_en: 'Taiwan, China', name_zh: '中国台湾', lat: 23.7, lng: 121, status: 'Supported', labelOffset: [118, 28] },
  { code: 'NZ', name_en: 'New Zealand', name_zh: '新西兰', lat: -41, lng: 171, status: 'Supported', labelOffset: [94, -18] },
  { code: 'SE', name_en: 'Sweden', name_zh: '瑞典', lat: 62, lng: 18, status: 'Scheduled', labelOffset: [-108, -82] },
  { code: 'JP', name_en: 'Japan', name_zh: '日本', lat: 36, lng: 138, status: 'Supported', labelOffset: [112, -18] },
  { code: 'SG', name_en: 'Singapore', name_zh: '新加坡', lat: 1.35, lng: 103.8, status: 'Scheduled', labelOffset: [-80, 92] },
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
