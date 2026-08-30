import classificationData from './data/country-classifications.json' with { type: 'json' };

export type WhoRegionCode = 'AFR' | 'AMR' | 'EMR' | 'EUR' | 'SEAR' | 'WPR';
export type WorldBankRegionCode = 'EAS' | 'ECS' | 'LCN' | 'MEA' | 'NAC' | 'SAS' | 'SSF';
export type IncomeGroupCode = 'LIC' | 'LMC' | 'UMC' | 'HIC';

export interface CountryClassification {
  who_region: WhoRegionCode;
  wb_region: WorldBankRegionCode;
  income_group: IncomeGroupCode;
}

export const WHO_REGION_LABELS: Record<WhoRegionCode, { en: string; zh: string }> = {
  AFR: { en: 'African Region', zh: '非洲区域' },
  AMR: { en: 'Region of the Americas', zh: '美洲区域' },
  EMR: { en: 'Eastern Mediterranean Region', zh: '东地中海区域' },
  EUR: { en: 'European Region', zh: '欧洲区域' },
  SEAR: { en: 'South-East Asia Region', zh: '东南亚区域' },
  WPR: { en: 'Western Pacific Region', zh: '西太平洋区域' },
};

export const WORLD_BANK_REGION_LABELS: Record<WorldBankRegionCode, { en: string; zh: string }> = {
  EAS: { en: 'East Asia & Pacific', zh: '东亚与太平洋' },
  ECS: { en: 'Europe & Central Asia', zh: '欧洲与中亚' },
  LCN: { en: 'Latin America & Caribbean', zh: '拉丁美洲与加勒比' },
  MEA: { en: 'Middle East, North Africa, Afghanistan & Pakistan', zh: '中东、北非、阿富汗与巴基斯坦' },
  NAC: { en: 'North America', zh: '北美' },
  SAS: { en: 'South Asia', zh: '南亚' },
  SSF: { en: 'Sub-Saharan Africa', zh: '撒哈拉以南非洲' },
};

export const INCOME_GROUP_LABELS: Record<IncomeGroupCode, { en: string; zh: string }> = {
  LIC: { en: 'Low income', zh: '低收入' },
  LMC: { en: 'Lower middle income', zh: '中低收入' },
  UMC: { en: 'Upper middle income', zh: '中高收入' },
  HIC: { en: 'High income', zh: '高收入' },
};

const classifications = classificationData.countries as Record<string, CountryClassification>;

export const COUNTRY_CLASSIFICATION_METADATA = {
  updatedAt: classificationData.updated_at,
  worldBankFiscalYear: classificationData.world_bank_fiscal_year,
  sources: classificationData.sources,
};

export function getCountryClassification(code: string): CountryClassification | undefined {
  const normalized = code.trim().toUpperCase();
  return classifications[normalized] ?? classifications[normalized.split('-')[0]];
}
