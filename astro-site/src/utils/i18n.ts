// src/utils/i18n.ts
// Lightweight localStorage-based i18n utility.
// All translatable strings live here; Header.astro's script calls updateLang() to swap them.

export type Lang = 'en' | 'zh';

export const STRINGS = {
  // ── Nav / layout ──────────────────────────────────────────
  home: { en: 'Home', zh: '首页' },
  countries: { en: 'Countries', zh: '国家' },
  diseases: { en: 'Diseases', zh: '疾病' },
  reports: { en: 'Reports', zh: '报告' },

  // ── Homepage ───────────────────────────────────────────────
  heroTitle: {
    en: 'Global Infectious Disease Surveillance',
    zh: '全球传染病监测与报告',
  },
  heroSubtitle: {
    en: 'AI-powered epidemiological analysis across countries and diseases.',
    zh: 'AI 驱动的跨国家、跨疾病流行病学分析平台。',
  },
  totalCases: { en: 'Total Cases', zh: '累计病例' },
  totalDeaths: { en: 'Total Deaths', zh: '累计死亡' },
  totalReports: { en: 'Reports Generated', zh: '已生成报告' },
  totalDiseases: { en: 'Diseases Tracked', zh: '追踪疾病数' },
  latestReports: { en: 'Latest Reports', zh: '最新报告' },
  allCountries: { en: 'All Countries', zh: '所有国家' },
  allDiseases: { en: 'Disease Directory', zh: '疾病目录' },
  viewReport: { en: 'View Report', zh: '查看报告' },
  viewAll: { en: 'View All', zh: '查看全部' },
  downloadCsv: { en: 'Download CSV', zh: '下载 CSV' },
  downloadJson: { en: 'Download JSON', zh: '下载 JSON' },

  // ── Country page ───────────────────────────────────────────
  countryOverview: { en: 'Country Overview', zh: '国家概览' },
  epidemicCurve: { en: 'Epidemic Curve', zh: '流行曲线' },
  diseaseHeatmap: { en: 'Disease Heatmap', zh: '疾病热图' },
  comparisonTable: { en: 'Disease Comparison', zh: '疾病对比表格' },
  recentReports: { en: 'Recent Reports', zh: '近期报告' },
  dateRange: { en: 'Data Range', zh: '数据区间' },
  topDiseases: { en: 'Top Diseases by Cases', zh: '按病例数排名的疾病' },

  // ── Disease page ───────────────────────────────────────────
  diseaseDetail: { en: 'Disease Detail', zh: '疾病详情' },
  icdCode: { en: 'ICD Code', zh: 'ICD 编码' },
  category: { en: 'Category', zh: '分类' },
  totalCasesGlobal: { en: 'Total Cases (All Countries)', zh: '总病例数（所有国家）' },
  peakMonth: { en: 'Peak Month', zh: '峰值月份' },
  latestCount: { en: 'Latest Period', zh: '最新报告周期' },
  trend: { en: 'Trend', zh: '趋势' },

  // ── Report page ────────────────────────────────────────────
  reportPeriod: { en: 'Report Period', zh: '报告周期' },
  generatedAt: { en: 'Generated At', zh: '生成时间' },
  qualityScore: { en: 'Quality Score', zh: '质量评分' },
  aiModel: { en: 'AI Model', zh: 'AI 模型' },
  keyFindings: { en: 'Key Findings', zh: '关键发现' },
  sections: { en: 'Report Sections', zh: '报告章节' },

  // ── Table columns ──────────────────────────────────────────
  disease: { en: 'Disease', zh: '疾病' },
  cases: { en: 'Cases', zh: '病例数' },
  deaths: { en: 'Deaths', zh: '死亡数' },
  incidenceRate: { en: 'Incidence Rate', zh: '发病率' },
  mortalityRate: { en: 'Mortality Rate', zh: '死亡率' },
  cfr: { en: 'CFR (%)', zh: '病死率 (%)' },
  change: { en: 'Change', zh: '变化' },

  // ── Category labels ────────────────────────────────────────
  bacterial: { en: 'Bacterial', zh: '细菌性' },
  viral: { en: 'Viral', zh: '病毒性' },
  parasitic: { en: 'Parasitic', zh: '寄生虫' },
  fungal: { en: 'Fungal', zh: '真菌性' },
  other: { en: 'Other', zh: '其他' },

  // ── Misc ───────────────────────────────────────────────────
  noData: { en: 'No data available', zh: '暂无数据' },
  loading: { en: 'Loading…', zh: '加载中…' },
  search: { en: 'Search', zh: '搜索' },
  filterByCategory: { en: 'Filter by category', zh: '按分类筛选' },
  all: { en: 'All', zh: '全部' },
  cases_unit: { en: 'cases', zh: '例' },
  deaths_unit: { en: 'deaths', zh: '人' },
  prev: { en: 'Previous', zh: '上一篇' },
  next: { en: 'Next', zh: '下一篇' },
} as const;

export type StringKey = keyof typeof STRINGS;

/** Get translated string. Defaults to English if lang is not provided. */
export function t(key: StringKey, lang: Lang = 'en'): string {
  return STRINGS[key][lang];
}

/** Format a number to locale string with compact notation */
export function fmtNumber(n: number | null | undefined, lang: Lang = 'en'): string {
  if (n == null) return '—';
  const locale = lang === 'zh' ? 'zh-CN' : 'en-US';
  if (Math.abs(n) >= 1_000_000)
    return new Intl.NumberFormat(locale, { notation: 'compact', maximumFractionDigits: 1 }).format(n);
  return new Intl.NumberFormat(locale).format(n);
}

/** Format a rate to fixed decimal */
export function fmtRate(n: number | null | undefined): string {
  if (n == null) return '—';
  return n.toFixed(4);
}

/** Category display name */
export function categoryLabel(cat: string, lang: Lang = 'en'): string {
  const map: Record<string, StringKey> = {
    Bacterial: 'bacterial',
    Viral: 'viral',
    Parasitic: 'parasitic',
    Fungal: 'fungal',
  };
  const key = map[cat];
  return key ? t(key, lang) : t('other', lang);
}

/** Tailwind class for category badge */
export function categoryBadgeClass(cat: string): string {
  const map: Record<string, string> = {
    Bacterial: 'badge-bacterial',
    Viral: 'badge-viral',
    Parasitic: 'badge-parasitic',
    Fungal: 'badge-fungal',
  };
  return `inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${map[cat] ?? 'badge-other'}`;
}
