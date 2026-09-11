// src/utils/i18n.ts
// Lightweight localStorage-based i18n utility.
// All translatable strings live here; Header.astro's script calls updateLang() to swap them.

/** Supported interface locales.  `fr` covers both France and Canadian users. */
export type Lang = 'en' | 'zh' | 'fr' | 'fr';

export const STRINGS = {
  // ── Nav / layout ──────────────────────────────────────────
  home: { en: 'Home', zh: '首页', fr: 'Accueil' },
  countries: { en: 'Countries', zh: '国家', fr: 'Pays' },
  diseases: { en: 'Diseases', zh: '疾病', fr: 'Maladies' },
  reports: { en: 'Reports', zh: '报告', fr: 'Rapports' },

  // ── Homepage ───────────────────────────────────────────────
  heroTitle: {
    en: 'Global Infectious Disease Surveillance',
    zh: '全球传染病监测与报告', fr: 'Surveillance mondiale des maladies infectieuses',
  },
  heroSubtitle: {
    en: 'AI-powered epidemiological analysis across countries and diseases.',
    zh: 'AI 驱动的跨国家、跨疾病流行病学分析平台。', fr: 'Plateforme d’analyse épidémiologique par IA, pour tous les pays et toutes les maladies.',
  },
  totalCases: { en: 'Total Cases', zh: '累计病例', fr: 'Cas cumulés' },
  totalDeaths: { en: 'Total Deaths', zh: '累计死亡', fr: 'Décès cumulés' },
  totalReports: { en: 'Reports Generated', zh: '已生成报告', fr: 'Rapports générés' },
  totalDiseases: { en: 'Diseases Tracked', zh: '追踪疾病数', fr: 'Maladies suivies' },
  latestReports: { en: 'Latest Reports', zh: '最新报告', fr: 'Derniers rapports' },
  allCountries: { en: 'All Countries', zh: '所有国家', fr: 'Tous les pays' },
  allDiseases: { en: 'Disease Directory', zh: '疾病目录', fr: 'Répertoire des maladies' },
  viewReport: { en: 'View Report', zh: '查看报告', fr: 'Voir le rapport' },
  viewAll: { en: 'View All', zh: '查看全部', fr: 'Voir tout' },
  downloadCsv: { en: 'Download CSV', zh: '下载 CSV', fr: 'Télécharger le CSV' },
  downloadJson: { en: 'Download JSON', zh: '下载 JSON', fr: 'Télécharger le JSON' },

  // ── Country page ───────────────────────────────────────────
  countryOverview: { en: 'Country Overview', zh: '国家概览', fr: 'Vue d’ensemble du pays' },
  epidemicCurve: { en: 'Epidemic Curve', zh: '流行曲线', fr: 'Courbe épidémique' },
  diseaseHeatmap: { en: 'Disease Heatmap', zh: '疾病热图', fr: 'Carte thermique des maladies' },
  comparisonTable: { en: 'Disease Comparison', zh: '疾病对比表格', fr: 'Comparaison des maladies' },
  recentReports: { en: 'Recent Reports', zh: '近期报告', fr: 'Rapports récents' },
  dateRange: { en: 'Data Range', zh: '数据区间', fr: 'Période des données' },
  topDiseases: { en: 'Top Diseases by Cases', zh: '按病例数排名的疾病', fr: 'Maladies les plus fréquentes' },

  // ── Disease page ───────────────────────────────────────────
  diseaseDetail: { en: 'Disease Detail', zh: '疾病详情', fr: 'Détails de la maladie' },
  icdCode: { en: 'ICD Code', zh: 'ICD 编码', fr: 'Code CIM' },
  category: { en: 'Category', zh: '分类', fr: 'Catégorie' },
  totalCasesGlobal: { en: 'Total Cases (All Countries)', zh: '总病例数（所有国家）', fr: 'Cas totaux (tous les pays)' },
  peakMonth: { en: 'Peak Month', zh: '峰值月份', fr: 'Mois du pic' },
  latestCount: { en: 'Latest Period', zh: '最新报告周期', fr: 'Période la plus récente' },
  trend: { en: 'Trend', zh: '趋势', fr: 'Tendance' },

  // ── Report page ────────────────────────────────────────────
  reportPeriod: { en: 'Report Period', zh: '报告周期', fr: 'Période du rapport' },
  generatedAt: { en: 'Generated At', zh: '生成时间', fr: 'Généré le' },
  qualityScore: { en: 'Quality Score', zh: '质量评分', fr: 'Score de qualité' },
  aiModel: { en: 'AI Model', zh: 'AI 模型', fr: 'Modèle d’IA' },
  keyFindings: { en: 'Key Findings', zh: '关键发现', fr: 'Résultats clés' },
  sections: { en: 'Report Sections', zh: '报告章节', fr: 'Sections du rapport' },

  // ── Table columns ──────────────────────────────────────────
  disease: { en: 'Disease', zh: '疾病', fr: 'Maladie' },
  cases: { en: 'Cases', zh: '病例数', fr: 'Cas' },
  deaths: { en: 'Deaths', zh: '死亡数', fr: 'Décès' },
  incidenceRate: { en: 'Incidence Rate', zh: '发病率', fr: 'Taux d’incidence' },
  mortalityRate: { en: 'Mortality Rate', zh: '死亡率', fr: 'Taux de mortalité' },
  cfr: { en: 'CFR (%)', zh: '病死率 (%)', fr: 'Létalité (%)' },
  change: { en: 'Change', zh: '变化', fr: 'Variation' },

  // ── Category labels ────────────────────────────────────────
  bacterial: { en: 'Bacterial', zh: '细菌性', fr: 'Bactérienne' },
  viral: { en: 'Viral', zh: '病毒性', fr: 'Virale' },
  parasitic: { en: 'Parasitic', zh: '寄生虫', fr: 'Parasitaire' },
  fungal: { en: 'Fungal', zh: '真菌性', fr: 'Fongique' },
  other: { en: 'Other', zh: '其他', fr: 'Autre' },

  // ── Misc ───────────────────────────────────────────────────
  noData: { en: 'No data available', zh: '暂无数据', fr: 'Aucune donnée disponible' },
  loading: { en: 'Loading…', zh: '加载中…', fr: 'Chargement…' },
  search: { en: 'Search', zh: '搜索', fr: 'Rechercher' },
  filterByCategory: { en: 'Filter by category', zh: '按分类筛选', fr: 'Filtrer par catégorie' },
  all: { en: 'All', zh: '全部', fr: 'Toutes' },
  cases_unit: { en: 'cases', zh: '例', fr: 'cas' },
  deaths_unit: { en: 'deaths', zh: '人', fr: 'décès' },
  prev: { en: 'Previous', zh: '上一篇', fr: 'Précédent' },
  next: { en: 'Next', zh: '下一篇', fr: 'Suivant' },
} as const;

export type StringKey = keyof typeof STRINGS;

/** Get translated string. Defaults to English if lang is not provided. */
export function t(key: StringKey, lang: Lang = 'en'): string {
  return STRINGS[key][lang] ?? STRINGS[key].en;
}

/** Format a number to locale string with compact notation */
export function fmtNumber(n: number | null | undefined, lang: Lang = 'en'): string {
  if (n == null) return '—';
  const locale = lang === 'zh' ? 'zh-CN' : lang === 'fr' ? 'fr-FR' : 'en-US';
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
  return `inline-flex items-center rounded-none px-2 py-0.5 text-xs font-medium ${map[cat] ?? 'badge-other'}`;
}
