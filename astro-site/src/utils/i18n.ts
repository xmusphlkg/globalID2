// src/utils/i18n.ts
// Lightweight localStorage-based i18n utility.
// All translatable strings live here; Header.astro's script calls updateLang() to swap them.

/** Supported interface locales. `fr` covers both France and Canadian users. */
export const SUPPORTED_LANGS = ['en', 'fr', 'zh'] as const;
export type Lang = typeof SUPPORTED_LANGS[number];
/**
 * A user-facing locale preference can carry a regional variant while the
 * content language remains shared.  `fr-CA` currently reuses the reviewed
 * French catalogue and `/fr` routes, but keeps the preference explicit so a
 * Canadian terminology/format pass can be added without another URL tree.
 */
export const LOCALE_PREFERENCES = ['en', 'fr', 'fr-CA', 'zh'] as const;
export type LocalePreference = typeof LOCALE_PREFERENCES[number];

export interface LocaleDefinition {
  code: Lang;
  bcp47: string;
  pathPrefix: '' | '/fr' | '/zh';
  nativeName: string;
  names: Record<Lang, string>;
}

/** Single locale registry used by routes, selectors, dates, and future locales. */
export const LOCALES: readonly LocaleDefinition[] = [
  { code: 'en', bcp47: 'en', pathPrefix: '', nativeName: 'English', names: { en: 'English', fr: 'anglais', zh: '英语' } },
  { code: 'fr', bcp47: 'fr', pathPrefix: '/fr', nativeName: 'Français', names: { en: 'French', fr: 'français', zh: '法语' } },
  { code: 'zh', bcp47: 'zh-CN', pathPrefix: '/zh', nativeName: '中文', names: { en: 'Chinese', fr: 'chinois', zh: '中文' } },
] as const;

export interface LocaleVariant {
  id: LocalePreference;
  contentLanguage: Lang;
  bcp47: string;
  pathPrefix: '' | '/fr' | '/zh';
  nativeName: string;
  names: Record<Lang, string>;
}

/** Selector options, including a regional French preference without duplicating static routes. */
export const LOCALE_VARIANTS: readonly LocaleVariant[] = [
  ...LOCALES.map((locale) => ({
    id: locale.code,
    contentLanguage: locale.code,
    bcp47: locale.bcp47,
    pathPrefix: locale.pathPrefix,
    nativeName: locale.nativeName,
    names: locale.names,
  })),
  {
    id: 'fr-CA',
    contentLanguage: 'fr',
    bcp47: 'fr-CA',
    pathPrefix: '/fr',
    nativeName: 'Français (Canada)',
    names: { en: 'Canadian French', fr: 'français canadien', zh: '加拿大法语' },
  },
] as const;

export function isLocalePreference(value: unknown): value is LocalePreference {
  return typeof value === 'string' && LOCALE_PREFERENCES.includes(value as LocalePreference);
}

export function normalizeLocalePreference(value: unknown): LocalePreference {
  if (isLocalePreference(value)) return value;
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'fr-ca' || normalized === 'fr_ca' || normalized === 'français canadien') return 'fr-CA';
  if (normalized === 'fr' || normalized === 'fr-fr' || normalized === 'french' || normalized === 'français') return 'fr';
  if (normalized === 'zh' || normalized === 'zh-cn' || normalized === 'cn' || normalized === '中文') return 'zh';
  return 'en';
}

export function contentLanguageForPreference(value: LocalePreference): Lang {
  return value === 'fr-CA' ? 'fr' : value;
}

export function localeVariant(value: LocalePreference): LocaleVariant {
  const variant = LOCALE_VARIANTS.find((item) => item.id === value);
  if (!variant) throw new Error(`Unsupported locale preference: ${value}`);
  return variant;
}

export const isLang = (value: unknown): value is Lang =>
  typeof value === 'string' && SUPPORTED_LANGS.includes(value as Lang);

export const localeDefinition = (lang: Lang): LocaleDefinition => {
  const definition = LOCALES.find((item) => item.code === lang);
  if (!definition) throw new Error(`Unsupported locale: ${lang}`);
  return definition;
};

/** BCP 47 tag for browser and Intl APIs; keeps locale formatting registry-driven. */
export const localeTag = (lang: Lang): string => localeDefinition(lang).bcp47;

export function formatLocaleNumber(
  value: number | null | undefined,
  lang: Lang = 'en',
  options: Intl.NumberFormatOptions = {},
): string {
  if (value == null || !Number.isFinite(value)) return '—';
  return new Intl.NumberFormat(localeTag(lang), options).format(value);
}

export function formatLocaleDate(
  value: string | Date | null | undefined,
  lang: Lang = 'en',
  options: Intl.DateTimeFormatOptions = {},
): string {
  if (!value) return '—';
  const parsed = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat(localeTag(lang), options).format(parsed);
}

export const stripLocalePrefix = (path: string): string =>
  path.replace(/^\/(?:zh|fr)(?=\/|$)/, '') || '/';

export const localizedPath = (path: string, lang: Lang): string => {
  const normalized = stripLocalePrefix(path);
  const prefix = localeDefinition(lang).pathPrefix;
  return prefix ? (normalized === '/' ? `${prefix}/` : `${prefix}${normalized}`) : normalized;
};

/** Strict, typed translator for co-located copy. Every supported locale is required. */
export const translateLocalized = <T>(lang: Lang, values: Record<Lang, T>): T => values[lang];

/** Compatibility-friendly strict translator for existing en/zh/fr component copy. */
export const createTranslator = (lang: Lang) => <T>(en: T, zh: T, fr: T): T =>
  translateLocalized(lang, { en, zh, fr });

export type LocalizedText = Partial<Record<Lang, string>> & Pick<Record<Lang, string>, 'en'>;

/**
 * Select generated or editorial text without silently substituting English on a
 * localized page. Build-time/static callers can opt into `required` so missing
 * translations fail publication; live catalogues receive an explicit localized
 * pending message instead of an English-looking French result.
 */
export function localizedDynamicText(
  lang: Lang,
  values: LocalizedText,
  options: { context?: string; required?: boolean } = {},
): string {
  const exact = values[lang]?.trim();
  if (exact) return exact;
  if (options.required) {
    throw new Error(`Missing ${lang} translation${options.context ? ` for ${options.context}` : ''}`);
  }
  if (lang === 'fr') return options.context
    ? `Traduction française en attente pour ${options.context}.`
    : 'Traduction française en attente.';
  if (lang === 'zh') return options.context
    ? `${options.context}的中文翻译待补充。`
    : '中文翻译待补充。';
  return values.en;
}

const FRENCH_REGION_OVERRIDES: Record<string, string> = {
  'CN-AH': 'Anhui, Chine', 'CN-BJ': 'Pékin, Chine', 'CN-CQ': 'Chongqing, Chine',
  'CN-FJ': 'Fujian, Chine', 'CN-GD': 'Guangdong, Chine', 'CN-GS': 'Gansu, Chine',
  'CN-GX': 'Guangxi, Chine', 'CN-GZ': 'Guizhou, Chine', 'CN-HA': 'Henan, Chine',
  'CN-HB': 'Hubei, Chine', 'CN-HE': 'Hebei, Chine', 'CN-HI': 'Hainan, Chine',
  'CN-HL': 'Heilongjiang, Chine', 'CN-HN': 'Hunan, Chine', 'CN-JL': 'Jilin, Chine',
  'CN-JS': 'Jiangsu, Chine', 'CN-JX': 'Jiangxi, Chine', 'CN-LN': 'Liaoning, Chine',
  'CN-NM': 'Mongolie-Intérieure, Chine', 'CN-NX': 'Ningxia, Chine', 'CN-QH': 'Qinghai, Chine',
  'CN-SC': 'Sichuan, Chine', 'CN-SD': 'Shandong, Chine', 'CN-SH': 'Shanghai, Chine',
  'CN-SN': 'Shaanxi, Chine', 'CN-SX': 'Shanxi, Chine', 'CN-TJ': 'Tianjin, Chine',
  'CN-XJ': 'Xinjiang, Chine', 'CN-XZ': 'Tibet, Chine', 'CN-YN': 'Yunnan, Chine', 'CN-ZJ': 'Zhejiang, Chine',
  'AU-ACT': 'Territoire de la capitale australienne', 'AU-NSW': 'Nouvelle-Galles du Sud',
  'AU-NT': 'Territoire du Nord', 'AU-QLD': 'Queensland', 'AU-SA': 'Australie-Méridionale',
  'AU-TAS': 'Tasmanie', 'AU-VIC': 'Victoria', 'AU-WA': 'Australie-Occidentale', 'CA-ON': 'Ontario, Canada',
};

/** Localize ISO country/region codes; subdivisions use the shared override table. */
export function localizedRegionName(
  lang: Lang,
  code: string | null | undefined,
  names: Partial<Record<Lang, string>> = {},
): string {
  const normalizedCode = String(code ?? '').toUpperCase();
  const supplied = names[lang]?.trim();
  if (supplied) return supplied;
  if (lang === 'fr' && FRENCH_REGION_OVERRIDES[normalizedCode]) return FRENCH_REGION_OVERRIDES[normalizedCode];
  if (/^[A-Z]{2}$/.test(normalizedCode)) {
    try {
      const display = new Intl.DisplayNames([localeDefinition(lang).bcp47], { type: 'region' }).of(normalizedCode);
      if (display && display !== normalizedCode) return display;
    } catch {
      // Non-ISO two-letter codes are handled by the explicit missing-name path below.
    }
  }
  if (lang === 'fr') throw new Error(`Missing French region name: ${normalizedCode || 'unknown'}`);
  return names.en?.trim() || normalizedCode;
}

export const STUDY_TYPE_LABELS: Record<string, Record<Lang, string>> = {
  'Case-control study': { en: 'Case-control study', zh: '病例对照研究', fr: 'Étude cas-témoins' },
  'Cohort study': { en: 'Cohort study', zh: '队列研究', fr: 'Étude de cohorte' },
  'Cross-sectional study': { en: 'Cross-sectional study', zh: '横断面研究', fr: 'Étude transversale' },
  'Ecological study': { en: 'Ecological study', zh: '生态学研究', fr: 'Étude écologique' },
  'Genomic study': { en: 'Genomic study', zh: '基因组研究', fr: 'Étude génomique' },
  Guideline: { en: 'Guideline', zh: '指南', fr: 'Recommandation' },
  'Journal article': { en: 'Journal article', zh: '期刊论文', fr: 'Article de revue' },
  'Mathematical modelling': { en: 'Mathematical modelling', zh: '数学建模', fr: 'Modélisation mathématique' },
  'Outbreak investigation': { en: 'Outbreak investigation', zh: '暴发调查', fr: 'Enquête sur une flambée' },
  'Randomised controlled trial': { en: 'Randomised controlled trial', zh: '随机对照试验', fr: 'Essai contrôlé randomisé' },
  'Systematic review': { en: 'Systematic review', zh: '系统综述', fr: 'Revue systématique' },
};

export const RESEARCH_TOPIC_LABELS: Record<string, Record<Lang, string>> = {
  'Antimicrobial resistance': { en: 'Antimicrobial resistance', zh: '抗微生物药物耐药性', fr: 'Résistance aux antimicrobiens' },
  'Climate and environment': { en: 'Climate and environment', zh: '气候与环境', fr: 'Climat et environnement' },
  Diagnostics: { en: 'Diagnostics', zh: '诊断', fr: 'Diagnostic' },
  'Genomic epidemiology': { en: 'Genomic epidemiology', zh: '基因组流行病学', fr: 'Épidémiologie génomique' },
  'Health policy': { en: 'Health policy', zh: '卫生政策', fr: 'Politique de santé' },
  'One Health': { en: 'One Health', zh: '同一健康', fr: 'Une seule santé' },
  'Outbreak investigation': { en: 'Outbreak investigation', zh: '暴发调查', fr: 'Enquête sur une flambée' },
  Surveillance: { en: 'Surveillance', zh: '监测', fr: 'Surveillance' },
  'Transmission dynamics': { en: 'Transmission dynamics', zh: '传播动力学', fr: 'Dynamique de transmission' },
  'Travel medicine': { en: 'Travel medicine', zh: '旅行医学', fr: 'Médecine des voyages' },
  Treatment: { en: 'Treatment', zh: '治疗', fr: 'Traitement' },
  Vaccination: { en: 'Vaccination', zh: '疫苗接种', fr: 'Vaccination' },
  'Vaccine effectiveness': { en: 'Vaccine effectiveness', zh: '疫苗有效性', fr: 'Efficacité vaccinale' },
  'Vaccine safety': { en: 'Vaccine safety', zh: '疫苗安全性', fr: 'Sécurité vaccinale' },
};

export function localizedStudyType(lang: Lang, value: string | null | undefined): string {
  const normalized = value?.trim() || 'Journal article';
  const labels = STUDY_TYPE_LABELS[normalized];
  if (labels) return labels[lang];
  if (lang === 'fr') throw new Error(`Missing French study-type label: ${normalized}`);
  return normalized;
}

export function localizedResearchTopic(lang: Lang, value: string): string {
  const labels = RESEARCH_TOPIC_LABELS[value];
  if (labels) return labels[lang];
  if (lang === 'fr') throw new Error(`Missing French research-topic label: ${value}`);
  return value;
}

export const RESEARCH_FACET_LABELS: Record<string, Record<Lang, string>> = {
  Virus: { en: 'Virus', zh: '病毒', fr: 'Virus' },
  Parasite: { en: 'Parasite', zh: '寄生虫', fr: 'Parasite' },
  Bacterium: { en: 'Bacterium', zh: '细菌', fr: 'Bactérie' },
  Fungus: { en: 'Fungus', zh: '真菌', fr: 'Champignon' },
  Children: { en: 'Children', zh: '儿童', fr: 'Enfants' },
  'Pregnant women': { en: 'Pregnant women', zh: '孕妇', fr: 'Femmes enceintes' },
  Infants: { en: 'Infants', zh: '婴儿', fr: 'Nourrissons' },
  Adolescents: { en: 'Adolescents', zh: '青少年', fr: 'Adolescents' },
  'Older adults': { en: 'Older adults', zh: '老年人', fr: 'Personnes âgées' },
  'Immunocompromised populations': { en: 'Immunocompromised populations', zh: '免疫功能低下人群', fr: 'Populations immunodéprimées' },
  'Healthcare workers': { en: 'Healthcare workers', zh: '医务人员', fr: 'Professionnels de santé' },
  'General population': { en: 'General population', zh: '一般人群', fr: 'Population générale' },
  Travellers: { en: 'Travellers', zh: '旅行者', fr: 'Voyageurs' },
};

export function localizedResearchFacet(lang: Lang, value: string): string {
  const labels = RESEARCH_FACET_LABELS[value];
  if (labels) return labels[lang];
  if (lang === 'fr') throw new Error(`Missing French research-facet label: ${value}`);
  return value;
}

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

/** Shared French UI fallbacks for legacy views that still use an English/Chinese pair. */
export const FR_UI: Record<string, string> = {
  Home: 'Accueil', Search: 'Rechercher', 'Open global search': 'Ouvrir la recherche globale',
  'Primary navigation': 'Navigation principale', 'Mobile navigation': 'Navigation mobile', Language: 'Langue',
  Data: 'Données', Situation: 'Situation', Research: 'Recherche', 'Methods & About': 'Méthodes et à propos',
  Countries: 'Pays', Diseases: 'Maladies', Downloads: 'Téléchargements', Reports: 'Rapports',
  'Open navigation menu': 'Ouvrir le menu de navigation', 'Close navigation menu': 'Fermer le menu de navigation',
  'Switch to dark theme': 'Passer au thème sombre', 'Switch to light theme': 'Passer au thème clair',
  'Global search': 'Recherche globale', 'Find data and evidence': 'Trouver des données et des preuves',
  'Close search': 'Fermer la recherche', 'Search results': 'Résultats de recherche', 'View all results': 'Voir tous les résultats',
  'No matching evidence found.': 'Aucun résultat correspondant.',
  'Signal analysis': 'Analyse des signaux', 'One row per source-native identity': 'Une ligne par identité propre à la source',
  'Read methodology': 'Lire la méthodologie', 'Signal filters': 'Filtres des signaux',
  'Disease, geography, series…': 'Maladie, géographie, série…', State: 'État', 'All states': 'Tous les états',
  Source: 'Source', 'All sources': 'Toutes les sources', Tag: 'Étiquette', 'All tags': 'Toutes les étiquettes',
  Increasing: 'En hausse', Unusual: 'Inhabituel', Respiratory: 'Respiratoire', 'Official match': 'Événement officiel associé',
  'Data status': 'État des données', 'All data states': 'Tous les états des données', Reset: 'Réinitialiser',
  Signal: 'Signal', 'Observed vs expected': 'Observé vs attendu', 'Statistical evidence': 'Preuves statistiques', 'Data readiness': 'État de préparation des données',
  Expand: 'Développer', expected: 'attendu', upper: 'limite supérieure', 'analyzed through': 'analysé jusqu’au', Observed: 'Observé', Expected: 'Attendu', '95% upper': 'Limite supérieure à 95 %',
  Model: 'Modèle', 'Detector tier': 'Niveau de détection', 'Raw p': 'p brut', Dispersion: 'Dispersion', Completeness: 'Complétude', 'Data through': 'Données jusqu’au', 'Latest received': 'Dernière réception',
  'Temporal relevance': 'Pertinence temporelle', Verification: 'Vérification', 'Verification basis': 'Base de vérification', 'Reporting lag': 'Retard de déclaration', days: 'jours', Fit: 'Ajustement',
  Interpretation: 'Interprétation', 'Open disease profile': 'Ouvrir la fiche maladie', 'Raw identity and evidence': 'Identité brute et preuves',
  'No trend points available.': 'Aucun point de tendance disponible.', 'No signals match the current filters.': 'Aucun signal ne correspond aux filtres actuels.',
  'Official evidence': 'Preuves officielles', 'Clustered event timelines': 'Chronologies d’événements regroupés', 'Context only': 'Contexte uniquement', 'Respiratory indicators remain separate': 'Les indicateurs respiratoires restent séparés',
  'Compare only mature source periods': 'Comparer uniquement les périodes sources matures',
  'usable series coverage': 'Couverture des séries utilisables', 'Ready feeds': 'Sources prêtes', 'Partial feeds': 'Sources partielles', 'Stale / failed': 'Obsolètes / en échec', 'Periods held back': 'Périodes retenues', 'Delayed series': 'Séries en retard',
  'Source data readiness': 'Préparation des données sources', 'Analysis through': 'Analyse jusqu’au', 'Readiness': 'Préparation', Status: 'État', 'series analyzed': 'séries analysées', 'held back': 'retenues', delayed: 'en retard',
  'Period archive': 'Archive des périodes', 'Situation Room': 'Salle de situation', 'New': 'Nouveaux', Persistent: 'Persistants', Resolved: 'Résolus', Active: 'Actifs', revision: 'révision',
  'Adjacent reports': 'Rapports adjacents', 'Previous period': 'Période précédente', 'All period reports': 'Tous les rapports de période', 'Next period': 'Période suivante', 'Download JSON': 'Télécharger le JSON',
  'Situation review': 'Revue de situation', "Today's review brief": 'Brief de revue du jour', 'Provisional periods excluded': 'Périodes provisoires exclues', 'Delayed series isolated': 'Séries en retard isolées', 'Attributed risk assessments': 'Évaluations des risques attribués', 'Unavailable feeds': 'Sources indisponibles',
  'Open full analysis →': 'Ouvrir l’analyse complète →',
  'Official notice': 'Avis officiel', 'Terms summary': 'Résumé des conditions', 'Project Links': 'Liens du projet', 'Copyright & reuse notice': 'Droits d’auteur et réutilisation', 'Source repository': 'Dépôt source', 'Issue tracker': 'Suivi des problèmes', 'Subscription center': 'Centre d’abonnement',
  'Service Terms': 'Conditions du service', 'GIDS Service Terms and Privacy Notice': 'Conditions du service et avis de confidentialité GIDS', 'Changelog': 'Historique des versions', 'What’s new in GIDS': 'Nouveautés de GIDS',
  Subscribe: 'S’abonner', 'Subscribe | GIDS': 'Abonnement | GIDS', 'GIDS subscription center': 'Centre d’abonnement GIDS',
  'Follow the surveillance updates that matter to you': 'Suivez les mises à jour de surveillance qui vous intéressent',
  Delivery: 'Livraison', 'Reports, Research Radar digests, and priority alerts': 'Rapports, synthèses Research Radar et alertes prioritaires',
  Filters: 'Filtres', 'Country, disease, topic, and study preferences': 'Préférences de pays, maladie, sujet et type d’étude',
  Control: 'Contrôle', 'One-click unsubscribe link': 'Lien de désabonnement en un clic', Preferences: 'Préférences', 'Create a subscription': 'Créer un abonnement',
  'Official GIDS service': 'Service officiel GIDS', 'Email address': 'Adresse e-mail', Frequency: 'Fréquence', 'Updates to receive': 'Mises à jour à recevoir',
  'All countries': 'Tous les pays', 'Leave empty for all': 'Laisser vide pour tout sélectionner', 'Search diseases': 'Rechercher une maladie', 'Research topics': 'Sujets de recherche',
  'For Research Radar; leave empty for all': 'Pour Research Radar ; laisser vide pour tout sélectionner', 'Study types': 'Types d’étude', 'Publication status': 'État de publication',
  'Leave empty for all approved research': 'Laisser vide pour toutes les recherches validées', 'I agree to receive GIDS subscription emails and accept the': 'J’accepte de recevoir les e-mails d’abonnement GIDS et les', '.': '.',
  'We only send the updates you choose. You can unsubscribe in any email.': 'Nous envoyons uniquement les mises à jour choisies. Vous pouvez vous désabonner depuis chaque e-mail.', Close: 'Fermer', 'Subscription received': 'Abonnement reçu', 'Check your inbox': 'Consultez votre boîte de réception', 'Trusted sender': 'Expéditeur approuvé',
  'Add this sender to your contacts or allowlist so future GIDS updates are not filtered as spam.': 'Ajoutez cet expéditeur à vos contacts ou à votre liste blanche afin que les prochaines mises à jour GIDS ne soient pas classées comme indésirables.',
  'Resend confirmation': 'Renvoyer la confirmation', Done: 'Terminé',
  'Countries and Regions': 'Pays et régions', 'Countries and Regions | GIDS': 'Pays et régions | GIDS',
  'Current coverage': 'Couverture actuelle', 'Coverage summary': 'Résumé de la couverture', Records: 'Enregistrements', 'Latest data': 'Dernières données',
  'Live geography layer': 'Couche géographique en direct', 'Data coverage map': 'Carte de couverture des données', 'Map status summary': 'Résumé de l’état de la carte',
  'countries live': 'pays actifs', 'regional feeds': 'sources régionales', 'Map legend': 'Légende de la carte', 'Country supported': 'Pays pris en charge', 'Province / region supported': 'Province / région prise en charge', Planned: 'Planifié', 'Not supported': 'Non pris en charge',
  'Countries and regions': 'Pays et régions', 'Filter countries and regions': 'Filtrer les pays et régions', 'Country or region': 'Pays ou région', 'Search by name or code': 'Rechercher par nom ou code', 'WHO region': 'Région OMS', 'World Bank region': 'Région de la Banque mondiale', 'Development level': 'Niveau de développement', 'All WHO regions': 'Toutes les régions OMS', 'All WB regions': 'Toutes les régions BM', 'All income groups': 'Tous les groupes de revenu', 'Filter by data status': 'Filtrer par état des données', All: 'Tous', Supported: 'Pris en charge', 'Reset filters': 'Réinitialiser les filtres', 'Official source': 'Source officielle', 'Source assessment in progress.': 'Évaluation de la source en cours.', 'No countries or regions match these filters.': 'Aucun pays ou région ne correspond à ces filtres.', Showing: 'Affichage', 'countries/regions': 'pays/régions', active: 'actifs',
  'China province-level datasets': 'Jeux de données provinciaux de Chine', 'Browse provinces →': 'Parcourir les provinces →',

  // ── Research Radar interface ──────────────────────────────
  'Literature intelligence': 'Veille bibliographique', 'Browse latest publications': 'Parcourir les publications récentes',
  'Research integrity alerts': 'Alertes d’intégrité de la recherche', 'Ask GIDS with cited evidence →': 'Interroger GIDS avec des preuves citées →',
  'Explore the evidence graph →': 'Explorer le graphe des preuves →', 'Browse historical baseline →': 'Parcourir la référence historique →',
  'Read the latest weekly brief →': 'Lire le dernier brief hebdomadaire →', 'At a glance': 'En bref', 'Public catalogue': 'Catalogue public',
  'Historical baseline': 'Référence historique', 'Papers · 7 days': 'Articles · 7 jours', 'Diseases · 7 days': 'Maladies · 7 jours',
  'Countries · 7 days': 'Pays · 7 jours', 'Reviews & guidelines · 7 days': 'Revues et recommandations · 7 jours',
  'Research collections': 'Collections de recherche', Explore: 'Explorer', 'Browse research collections': 'Parcourir les collections de recherche',
  'RSS feeds & subscriptions →': 'Flux RSS et abonnements →', Topics: 'Thèmes', Tools: 'Outils', 'Evidence graph': 'Graphe des preuves',
  'Stay current': 'Rester informé', 'Subscribe to a focused Research Radar feed': 'S’abonner à un flux Research Radar ciblé',
  'Subscribe to all research': 'S’abonner à toute la recherche', 'Editor’s selection': 'Sélection éditoriale', "Editor's selection": 'Sélection éditoriale',
  'Featured research': 'Recherches à la une', 'Monitoring context': 'Contexte de surveillance', 'Surveillance-linked Research': 'Recherche liée à la surveillance',
  'Open Situation Room →': 'Ouvrir la salle de situation →', 'Evidence synthesis': 'Synthèse des preuves', 'New Reviews & Guidelines': 'Nouvelles revues et recommandations',
  'Subscribe to this collection →': 'S’abonner à cette collection →', 'Literature momentum': 'Dynamique bibliographique', 'Emerging Topics': 'Thèmes émergents',
  '28-day attention': 'Attention sur 28 jours', Papers: 'Articles', Change: 'Variation', Share: 'Part', 'Historical database': 'Base historique',
  'Baseline literature across earlier eras': 'Littérature de référence des périodes antérieures', 'Research trends': 'Tendances de recherche',
  'How literature attention is changing': 'Évolution de l’attention bibliographique', 'Evidence library': 'Bibliothèque de preuves', 'Latest publications': 'Publications récentes',
  'Filter research articles': 'Filtrer les articles de recherche', 'Search title, disease, journal, or topic': 'Rechercher un titre, une maladie, une revue ou un thème',
  Disease: 'Maladie', 'All diseases': 'Toutes les maladies', Country: 'Pays', 'Study type': 'Type d’étude',
  'All study types': 'Tous les types d’étude', Topic: 'Thème', 'All topics': 'Tous les thèmes', 'Pathogen type': 'Type d’agent pathogène',
  'All pathogen types': 'Tous les types d’agents pathogènes', Population: 'Population', 'All populations': 'Toutes les populations', Journal: 'Revue',
  'All journals': 'Toutes les revues', Publisher: 'Éditeur', 'All publishers': 'Tous les éditeurs', 'Published from': 'Publié à partir du',
  'Published to': 'Publié jusqu’au', Collection: 'Collection', 'Peer reviewed': 'Évalué par les pairs', 'Approved preprints': 'Prépublications validées',
  'Open access only': 'Accès ouvert uniquement', 'Load more publications': 'Charger davantage de publications', 'No matching research': 'Aucune recherche correspondante',
  'Research Radar is ready': 'Research Radar est prêt', 'Try broadening one or more filters.': 'Essayez d’élargir un ou plusieurs filtres.',
  'The catalogue will populate after the first scheduled or manual literature synchronization.': 'Le catalogue sera alimenté après la première synchronisation bibliographique, planifiée ou manuelle.',
  Preprint: 'Prépublication', 'Open access': 'Accès ouvert', 'Journal unavailable': 'Revue indisponible',
  'Open article ↗': 'Ouvrir l’article ↗', 'Related surveillance': 'Surveillance associée', Type: 'Type', Scope: 'Portée', 'Not classified': 'Non classé',
  'Why it matters now': 'Pourquoi cette étude compte maintenant', 'Ask GIDS Research': 'Interroger la recherche GIDS', 'Evidence-grounded retrieval': 'Recherche fondée sur les preuves',
  'How Ask GIDS works': 'Comment fonctionne Ask GIDS', 'How it works': 'Fonctionnement', 'Parse the question': 'Analyser la question',
  'Disease, place, topic, and study design': 'Maladie, lieu, thème et plan d’étude', 'Rank published evidence': 'Classer les preuves publiées',
  'Explainable catalogue matching': 'Mise en correspondance explicable du catalogue', 'Add surveillance context': 'Ajouter le contexte de surveillance',
  'When a public time series is available': 'Lorsqu’une série chronologique publique est disponible', 'Research question': 'Question de recherche',
  'Find evidence': 'Trouver des preuves', 'Example questions': 'Questions d’exemple', 'Try a focused query': 'Essayez une requête ciblée',
  'Processed in this browser · not sent to a model · not stored by this page': 'Traité dans ce navigateur · envoyé à aucun modèle · non conservé par cette page',
  'Catalogue answer': 'Réponse du catalogue', 'What the indexed evidence shows': 'Ce que montrent les preuves indexées', 'Browse Research Radar': 'Parcourir Research Radar',
  'Answer counts': 'Comptage des réponses', 'Exact matches': 'Correspondances exactes', 'Background matches': 'Correspondances contextuelles',
  'Public signals': 'Signaux publics', 'Surveillance context': 'Contexte de surveillance', 'Epidemiological curve': 'Courbe épidémiologique', 'GIDS data': 'Données GIDS',
  'Current GIDS context': 'Contexte GIDS actuel', 'Linked public surveillance signals': 'Signaux publics de surveillance liés', 'Linked evidence': 'Preuves liées',
  'Cited evidence': 'Preuves citées', 'Relevant Research Radar records': 'Références Research Radar pertinentes', 'Source-level links': 'Liens au niveau de la source',
  'Journal article': 'Article de revue', 'Publisher unavailable': 'Éditeur indisponible',
  'Article type': 'Type d’article', Integrity: 'Intégrité', Corrected: 'Corrigé', 'Integrity update': 'Mise à jour d’intégrité',
  'This public record has a correction history': 'Cette référence publique possède un historique de correction', 'All integrity alerts →': 'Toutes les alertes d’intégrité →',
  'Publication version': 'Version de publication', 'A peer-reviewed version is available': 'Une version évaluée par les pairs est disponible',
  'This article has a linked preprint': 'Cet article possède une prépublication liée', 'Open peer-reviewed version →': 'Ouvrir la version évaluée par les pairs →',
  'Open linked preprint →': 'Ouvrir la prépublication liée →', 'Structured evidence summary': 'Résumé structuré des preuves', 'Related GIDS surveillance': 'Surveillance GIDS associée',
  'Active Situation Room context': 'Contexte actuel de la salle de situation', 'Geography unavailable': 'Zone géographique indisponible',
  'High-confidence geography · GIDS series available': 'Zone géographique à forte confiance · série GIDS disponible', 'View available GIDS surveillance': 'Voir la surveillance GIDS disponible',
  'Evidence relationships': 'Relations entre les preuves', 'Related research': 'Recherches associées', Pathogens: 'Agents pathogènes', Populations: 'Populations',
  'Geographic scope': 'Portée géographique', 'Original sources': 'Sources originales', 'DOI record ↗': 'Référence DOI ↗', 'Open access ↗': 'Accès ouvert ↗',
  'GIDS Discovery Score': 'Score de découverte GIDS', 'Summary transparency': 'Transparence du résumé',

  // ── Strict French coverage for public pages ──────────────
  'Browse countries and regions with available infectious disease surveillance data.': 'Parcourez les pays et régions disposant de données de surveillance des maladies infectieuses.',
  Scheduled: 'Planifié', 'See where GIDS has a country-level feed, a province-level feed, or a planned source.': 'Consultez les pays et régions pour lesquels GIDS dispose d’un flux national, d’un flux provincial ou d’une source planifiée.',
  'Hover a point for details · click a live point to open its page': 'Survolez un point pour afficher les détails · cliquez sur un point actif pour ouvrir sa page',
  'Marker positions use the bundled open GeoJSON boundary set and ISO country catalogue; province points are maintained as approximate administrative centroids.': 'Les positions des marqueurs utilisent le jeu ouvert de limites GeoJSON et le catalogue ISO intégrés ; les points provinciaux correspondent à des centroïdes administratifs approximatifs.',
  'Data temporarily unavailable': 'Données temporairement indisponibles', of: 'sur',
  'Analysis through · latest received · cohort readiness': 'Analyse jusqu’au · dernière réception · préparation de la cohorte',
  'Global surveillance review queue': 'File de revue de la surveillance mondiale', 'Report period': 'Période du rapport', Coverage: 'Couverture', Revision: 'Révision', Updated: 'Mis à jour',
  'Subscribe to GIDS disease surveillance alerts and report updates.': 'Abonnez-vous aux alertes de surveillance des maladies et aux mises à jour des rapports GIDS.',
  'Choose the countries, diseases, and update frequency you want to follow. You can unsubscribe at any time.': 'Choisissez les pays, les maladies et la fréquence de mise à jour que vous souhaitez suivre. Vous pouvez vous désabonner à tout moment.',
  'Review the dated events and verify the linked source record before using the findings.': 'Examinez les événements datés et vérifiez la référence source liée avant d’utiliser les résultats.',
  'The relationship comes from Crossref version metadata. Compare both records because findings and wording may have changed between versions.': 'Cette relation provient des métadonnées de version Crossref. Comparez les deux références, car les résultats et leur formulation peuvent avoir changé entre les versions.',
  'Correction event history': 'Historique des événements de correction', 'Verify source record ↗': 'Vérifier la référence source ↗',
  'A disease-only match provides background reading; it is not evidence about the signal geography or cause.': 'Une correspondance limitée à la maladie fournit un contexte documentaire ; elle ne constitue pas une preuve concernant la zone géographique ou la cause du signal.',
  'Literature context does not validate, explain, or change a surveillance signal. Exact and contextual relationships are shown separately.': 'Le contexte bibliographique ne valide, n’explique ni ne modifie un signal de surveillance. Les relations exactes et contextuelles sont présentées séparément.',
  'Ranked deterministically by shared disease, geography, topic, study design, and publication proximity. This is a discovery aid, not a quality judgment.': 'Classement déterministe selon la maladie, la zone géographique, le thème, le plan d’étude et la proximité de publication. Il s’agit d’une aide à la découverte, pas d’un jugement de qualité.',
  'A transparent discovery ranking, not a scientific quality or credibility score.': 'Un classement de découverte transparent, et non un score de qualité scientifique ou de crédibilité.',
  'Ask about a disease, country, study design, or public-health topic. Results come only from published Research Radar records and their verified public surveillance links.': 'Posez une question sur une maladie, un pays, un plan d’étude ou un sujet de santé publique. Les résultats proviennent uniquement des références publiées de Research Radar et de leurs liens de surveillance publique vérifiés.',
  'What does recent evidence say about pertussis resurgence?': 'Que disent les données récentes sur la résurgence de la coqueluche ?',
  'pertussis resurgence and vaccination': 'résurgence de la coqueluche et vaccination', 'Pertussis resurgence': 'Résurgence de la coqueluche',
  'dengue surveillance in Brazil': 'surveillance de la dengue au Brésil', 'Dengue in Brazil': 'Dengue au Brésil',
  'antimicrobial resistance systematic reviews': 'revues systématiques sur la résistance aux antimicrobiens', 'AMR reviews': 'Revues sur la résistance aux antimicrobiens',
  'Research Radar': 'Radar de recherche',
  'Find high-signal infectious disease research, organized for quick review across diseases, regions, and public-health topics.': 'Trouvez les recherches les plus pertinentes sur les maladies infectieuses, organisées pour une revue rapide par maladie, région et thème de santé publique.',
  'Research Radar at a glance': 'Research Radar en un coup d’œil', 'Published preprints': 'Prépublications publiées', 'Integrity alerts': 'Alertes d’intégrité',
  'Choose a stable RSS address for all research, or narrow it by disease, country, topic, study type, reviews and guidelines, or peer-review status. A count of 0 means no matching public records are available yet; the feed remains active for future releases.': 'Choisissez une adresse RSS stable pour l’ensemble de la recherche ou filtrez par maladie, pays, thème, type d’étude, revues et recommandations, ou statut d’évaluation par les pairs. Un total de 0 signifie qu’aucune référence publique correspondante n’est encore disponible ; le flux reste actif pour les prochaines publications.',
  'Published literature is linked to public monitoring context with exact and disease-only relationships clearly separated. These links provide background and do not validate or explain a signal.': 'La littérature publiée est reliée au contexte public de surveillance, en distinguant clairement les relations exactes de celles limitées à la maladie. Ces liens apportent du contexte et ne valident ni n’expliquent un signal.',
  'No public Research Radar article currently meets this relationship threshold.': 'Aucun article public de Research Radar n’atteint actuellement ce seuil de relation.',
  'Topics with rising publication attention over the latest 28-day window. Increased attention does not mean increased disease risk.': 'Thèmes dont l’attention bibliographique augmente sur les 28 derniers jours. Une attention accrue ne signifie pas un risque accru de maladie.',
  'Curated historical records supplement the recent literature stream and are labelled separately from newly indexed papers.': 'Des références historiques sélectionnées complètent le flux récent et sont identifiées séparément des articles nouvellement indexés.',
};

export function translateUi(value: string): string {
  const translated = FR_UI[value];
  if (!translated) throw new Error(`Missing French UI translation: ${value}`);
  return translated;
}

/** Get translated string. Defaults to English if lang is not provided. */
export function t(key: StringKey, lang: Lang = 'en'): string {
  return STRINGS[key][lang];
}

/** Format a number to locale string with compact notation */
export function fmtNumber(n: number | null | undefined, lang: Lang = 'en'): string {
  if (n == null) return '—';
  if (Math.abs(n) >= 1_000_000)
    return formatLocaleNumber(n, lang, { notation: 'compact', maximumFractionDigits: 1 });
  return formatLocaleNumber(n, lang);
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
