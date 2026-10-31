/** Localised labels and number/date formatting shared by the server pages and the map island. */

import type { AlertStatus, CountBasis, Locale, SituationAlertRecord } from '../../utils/situationTypes.ts';

const TAG: Record<Locale, string> = { en: 'en-US', zh: 'zh-CN', fr: 'fr-FR' };

type Text3 = Record<Locale, string>;
const t = (en: string, zh: string, fr: string): Text3 => ({ en, zh, fr });

export function formatDay(value: string | null | undefined, locale: Locale): string {
  if (!value) return '';
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(TAG[locale], { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
}

export function formatCount(value: number, locale: Locale): string {
  return value.toLocaleString(TAG[locale]);
}

export function formatSignedPercent(value: number, locale: Locale): string {
  const digits = Math.abs(value) < 10 ? 1 : 0;
  const formatted = value.toLocaleString(TAG[locale], {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  });
  return `${formatted}%`;
}

const regionNames = new Map<Locale, Intl.DisplayNames | null>();

function regionName(code: string, locale: Locale): string | null {
  if (!regionNames.has(locale)) {
    try {
      regionNames.set(locale, new Intl.DisplayNames([TAG[locale]], { type: 'region' }));
    } catch {
      regionNames.set(locale, null);
    }
  }
  try {
    const name = regionNames.get(locale)?.of(code.toUpperCase());
    return name && name !== code.toUpperCase() ? name : null;
  } catch {
    return null;
  }
}

type DiseaseNames = {
  disease_label: string;
  disease_name_en: string;
  disease_name_zh?: string | null;
};
type CountryNames = {
  country_code: string;
  country_name_en: string;
  country_name_zh?: string | null;
};
type SourceNames = {
  source_id: string;
  source_label_en?: string | null;
  source_label_zh?: string | null;
};

export function diseaseName(record: DiseaseNames, locale: Locale): string {
  const zh = locale === 'zh' ? record.disease_name_zh : null;
  return zh || record.disease_name_en || record.disease_label;
}

export function countryName(record: CountryNames, locale: Locale): string {
  if (locale === 'zh' && record.country_name_zh) return record.country_name_zh;
  if (locale === 'fr') return regionName(record.country_code, 'fr') || record.country_name_en || record.country_code;
  return record.country_name_en || record.country_code;
}

export function sourceLabel(record: SourceNames, locale: Locale): string {
  return (locale === 'zh' && record.source_label_zh) || record.source_label_en || record.source_id;
}

export function alertSummary(record: SituationAlertRecord, locale: Locale): string {
  if (locale === 'zh') return record.summary_zh || record.summary_en || '';
  if (locale === 'fr') return record.summary_fr || record.summary_en || '';
  return record.summary_en || '';
}

export const STATUS_LABELS: Record<AlertStatus, Text3> = {
  new: t('New', '新发', 'Nouveau'),
  ongoing: t('Ongoing', '持续', 'En cours'),
  imported: t('Imported', '输入', 'Importé'),
  ended: t('Ended', '结束', 'Terminé'),
  routine: t('Routine', '常规监测', 'Surveillance courante'),
};

const BASIS_LABELS: Record<CountBasis, Text3> = {
  confirmed: t('confirmed', '确诊', 'confirmés'),
  probable: t('probable', '可能', 'probables'),
  suspected: t('suspected', '疑似', 'suspects'),
  total: t('total', '合计', 'total'),
};

/** Counts exactly as the report states them: "9,704 cases (confirmed) · 4,687 deaths · as of 20 Oct 2026". */
export function countsLine(record: SituationAlertRecord, locale: Locale): string {
  const parts: string[] = [];
  const basis = record.count_basis ? ` (${BASIS_LABELS[record.count_basis][locale]})` : '';
  const caseWord = t('cases', '例病例', 'cas')[locale];
  const deathWord = t('deaths', '例死亡', 'décès')[locale];
  if (record.cases != null) {
    parts.push(locale === 'zh' ? `${formatCount(record.cases, locale)} ${caseWord}${basis}` : `${formatCount(record.cases, locale)} ${caseWord}${basis}`);
  }
  if (record.deaths != null) parts.push(`${formatCount(record.deaths, locale)} ${deathWord}`);
  if (parts.length && record.as_of) parts.push(`${t('as of', '截至', 'au')[locale]} ${formatDay(record.as_of, locale)}`);
  return parts.join(' · ');
}

export interface SituationCopy {
  filters: string;
  disease: string;
  country: string;
  source: string;
  window: string;
  any: string;
  reset: string;
  win7: string;
  win30: string;
  win90: string;
  winAll: string;
  listHeading: string;
  noResults: string;
  places: (n: number) => string;
  legendHeading: string;
  fresh: string;
  recent: string;
  older: string;
  stale: string;
  legendNote: string;
  detailHeading: string;
  pickHint: string;
  originalReport: string;
  quote: string;
  quoteWithheld: string;
  noCounts: string;
  localHeading: string;
  localLatest: string;
  localCurve: string;
  placesInOutbreak: string;
  unplaced: string;
  mapLoading: string;
  mapFailed: string;
  timelineFailed: string;
  mapLabel: string;
  stageTitle: string;
  updated: string;
  firstReported: string;
  alertsCount: (n: number) => string;
  sourceLine: string;
  weekly: string;
  monthly: string;
  methods: string;
  placesToggle: string;
  linked: (linked: number, total: number) => string;
  countryDossier: string;
  alertsHere: string;
  noAlertsHere: string;
  widenWindow: string;
  surveillanceHeading: string;
  nationalSeries: string;
  noNationalSeries: string;
  diseaseNotInSeries: (disease: string, count: number) => string;
  trackedDiseases: (n: number) => string;
  openCountry: string;
  openDisease: string;
  openResearch: (n: number) => string;
  vsPrevious: string;
  footprintLegend: string;
  seriesLegend: string;
  monitoringHeading: string;
  monitoringLead: string;
  close: string;
  breathe: string;
  activeLabel: string;
}

export const COPY: Record<Locale, SituationCopy> = {
  en: {
    filters: 'Filters',
    disease: 'Disease',
    country: 'Country',
    source: 'Source',
    window: 'Time window',
    any: 'All',
    reset: 'Reset filters',
    win7: 'Last 7 days',
    win30: 'Last 30 days',
    win90: 'Last 90 days',
    winAll: 'Whole period',
    listHeading: 'Places with news',
    noResults: 'No place matches these filters.',
    places: n => `${n} ${n === 1 ? 'place' : 'places'}`,
    legendHeading: 'Colour = recency of the latest report',
    fresh: 'Updated in the last 7 days',
    recent: '8 to 30 days ago',
    older: '31 to 90 days ago',
    stale: 'More than 90 days ago',
    legendNote: 'Colour and pulse show how recent the report is. A faster pulse is the last 7 days. Neither shows severity.',
    detailHeading: 'Alert timeline',
    pickHint: 'Select a place on the map or in the list to see the alerts reported for this outbreak.',
    originalReport: 'Original report',
    quote: 'From the report',
    quoteWithheld: 'No quotation is shown: this source does not allow reuse. Read the original report.',
    noCounts: 'The report gives no case or death count for this place.',
    localHeading: 'Local surveillance series',
    localLatest: 'Latest value in the GIDS series',
    localCurve: 'Open the country-disease curve',
    placesInOutbreak: 'Places in this outbreak',
    unplaced: 'Not drawn on the map',
    mapLoading: 'Loading map…',
    mapFailed: 'The map could not be loaded. The list and timeline still work.',
    timelineFailed: 'The alert timeline could not be loaded.',
    mapLabel: 'World map of places with recent outbreak reports',
    stageTitle: 'Situation map',
    updated: 'Updated',
    firstReported: 'First reported',
    alertsCount: n => `${n} ${n === 1 ? 'alert' : 'alerts'}`,
    sourceLine: 'Source',
    weekly: 'Weekly changes',
    monthly: 'Monthly archive',
    methods: 'How this works',
    placesToggle: 'Places',
    linked: (linked, total) => `${linked} of ${total} places match a national series`,
    countryDossier: 'Open this country',
    alertsHere: 'Alerts in this country',
    noAlertsHere: 'No alert in this window.',
    widenWindow: 'Show the whole period',
    surveillanceHeading: 'GIDS surveillance',
    nationalSeries: 'National surveillance series',
    noNationalSeries: 'GIDS does not publish a national surveillance series for this place.',
    diseaseNotInSeries: (disease, count) => `${disease} is not in the national series. GIDS tracks ${count} diseases here.`,
    trackedDiseases: n => `Tracks ${n} ${n === 1 ? 'disease' : 'diseases'}`,
    openCountry: 'Country surveillance',
    openDisease: 'Disease page',
    openResearch: n => `${n} ${n === 1 ? 'paper' : 'papers'}`,
    vsPrevious: 'vs previous period',
    footprintLegend: 'Shading: countries where GIDS publishes surveillance.',
    seriesLegend: 'Shading: latest GIDS value for the selected disease.',
    monitoringHeading: 'Monitoring products',
    monitoringLead: 'Periodic roundups with nothing unusual. They add no point.',
    close: 'Close',
    breathe: 'A pulse marks a report from the last 30 days.',
    activeLabel: 'Active outbreaks',
  },
  zh: {
    filters: '筛选',
    disease: '疾病',
    country: '国家',
    source: '来源',
    window: '时间范围',
    any: '全部',
    reset: '重置筛选',
    win7: '最近 7 天',
    win30: '最近 30 天',
    win90: '最近 90 天',
    winAll: '全部时段',
    listHeading: '有新消息的地点',
    noResults: '没有符合筛选条件的地点。',
    places: n => `${n} 个地点`,
    legendHeading: '颜色 = 最新报告的新旧',
    fresh: '最近 7 天有更新',
    recent: '8 至 30 天前',
    older: '31 至 90 天前',
    stale: '90 天以前',
    legendNote: '颜色和呼吸闪动都只表示报告有多新。更快的闪动是最近 7 天。两者都不表示严重程度。',
    detailHeading: '警报时间线',
    pickHint: '在地图或列表中选择一个地点，查看这起疫情的警报。',
    originalReport: '查看原始报告',
    quote: '报告原话',
    quoteWithheld: '不展示引文：该来源不允许再利用。请阅读原始报告。',
    noCounts: '报告没有给出该地点的病例或死亡数。',
    localHeading: '本站监测序列',
    localLatest: 'GIDS 序列最新值',
    localCurve: '打开该国该病的曲线',
    placesInOutbreak: '这起疫情涉及的地点',
    unplaced: '未能画在地图上',
    mapLoading: '正在加载地图…',
    mapFailed: '地图加载失败，列表和时间线仍可使用。',
    timelineFailed: '警报时间线加载失败。',
    mapLabel: '近期有疫情报告的地点世界地图',
    stageTitle: '态势地图',
    updated: '更新于',
    firstReported: '首次报告',
    alertsCount: n => `${n} 条警报`,
    sourceLine: '来源',
    weekly: '每周变化',
    monthly: '月度归档',
    methods: '方法说明',
    placesToggle: '地点',
    linked: (linked, total) => `${total} 个地点中有 ${linked} 个能对上国家级序列`,
    countryDossier: '打开该国',
    alertsHere: '该国的警报',
    noAlertsHere: '当前时间范围内没有警报。',
    widenWindow: '显示全部时段',
    surveillanceHeading: '本站监测',
    nationalSeries: '国家级监测序列',
    noNationalSeries: 'GIDS 没有发布该地的国家级监测序列。',
    diseaseNotInSeries: (disease, count) => `国家级序列未收录${disease}。GIDS 在这里跟踪 ${count} 种疾病。`,
    trackedDiseases: n => `跟踪 ${n} 种疾病`,
    openCountry: '国家监测页',
    openDisease: '疾病页',
    openResearch: n => `${n} 篇研究`,
    vsPrevious: '较上一周期',
    footprintLegend: '底色：GIDS 发布了监测数据的国家。',
    seriesLegend: '底色：所选疾病在 GIDS 中的最新值。',
    monitoringHeading: '监测产品',
    monitoringLead: '没有异常事件的定期汇总，不会在地图上增加点。',
    close: '关闭',
    breathe: '呼吸闪动表示报告来自最近 30 天。',
    activeLabel: '活跃疫情',
  },
  fr: {
    filters: 'Filtres',
    disease: 'Maladie',
    country: 'Pays',
    source: 'Source',
    window: 'Période',
    any: 'Tous',
    reset: 'Réinitialiser les filtres',
    win7: '7 derniers jours',
    win30: '30 derniers jours',
    win90: '90 derniers jours',
    winAll: 'Toute la période',
    listHeading: 'Lieux avec des nouvelles',
    noResults: 'Aucun lieu ne correspond à ces filtres.',
    places: n => `${n} ${n === 1 ? 'lieu' : 'lieux'}`,
    legendHeading: 'Couleur = fraîcheur du dernier rapport',
    fresh: 'Mis à jour dans les 7 derniers jours',
    recent: 'Il y a 8 à 30 jours',
    older: 'Il y a 31 à 90 jours',
    stale: 'Il y a plus de 90 jours',
    legendNote: 'La couleur et la pulsation indiquent la fraîcheur du rapport. Une pulsation plus rapide correspond aux 7 derniers jours. Ni l’une ni l’autre n’indique la gravité.',
    detailHeading: 'Chronologie des alertes',
    pickHint: 'Sélectionnez un lieu sur la carte ou dans la liste pour voir les alertes de cette flambée.',
    originalReport: 'Rapport original',
    quote: 'Extrait du rapport',
    quoteWithheld: 'Aucune citation affichée : cette source n’autorise pas la réutilisation. Consultez le rapport original.',
    noCounts: 'Le rapport ne donne ni cas ni décès pour ce lieu.',
    localHeading: 'Série de surveillance locale',
    localLatest: 'Dernière valeur de la série GIDS',
    localCurve: 'Ouvrir la courbe pays-maladie',
    placesInOutbreak: 'Lieux de cette flambée',
    unplaced: 'Non placés sur la carte',
    mapLoading: 'Chargement de la carte…',
    mapFailed: 'La carte n’a pas pu être chargée. La liste et la chronologie restent disponibles.',
    timelineFailed: 'La chronologie des alertes n’a pas pu être chargée.',
    mapLabel: 'Carte du monde des lieux avec des rapports récents de flambée',
    stageTitle: 'Carte de situation',
    updated: 'Mis à jour',
    firstReported: 'Premier signalement',
    alertsCount: n => `${n} ${n === 1 ? 'alerte' : 'alertes'}`,
    sourceLine: 'Source',
    weekly: 'Changements hebdomadaires',
    monthly: 'Archive mensuelle',
    methods: 'Méthodologie',
    placesToggle: 'Lieux',
    linked: (linked, total) => `${linked} lieux sur ${total} correspondent à une série nationale`,
    countryDossier: 'Ouvrir ce pays',
    alertsHere: 'Alertes dans ce pays',
    noAlertsHere: 'Aucune alerte sur cette période.',
    widenWindow: 'Afficher toute la période',
    surveillanceHeading: 'Surveillance GIDS',
    nationalSeries: 'Série nationale de surveillance',
    noNationalSeries: 'GIDS ne publie pas de série nationale de surveillance pour ce lieu.',
    diseaseNotInSeries: (disease, count) => `${disease} ne figure pas dans la série nationale. GIDS suit ${count} maladies ici.`,
    trackedDiseases: n => `Suit ${n} ${n === 1 ? 'maladie' : 'maladies'}`,
    openCountry: 'Surveillance du pays',
    openDisease: 'Page de la maladie',
    openResearch: n => `${n} ${n === 1 ? 'article' : 'articles'}`,
    vsPrevious: 'vs période précédente',
    footprintLegend: 'Teinte : pays pour lesquels GIDS publie une surveillance.',
    seriesLegend: 'Teinte : dernière valeur GIDS pour la maladie choisie.',
    monitoringHeading: 'Produits de surveillance',
    monitoringLead: 'Synthèses périodiques sans événement inhabituel. Elles n’ajoutent aucun point.',
    close: 'Fermer',
    breathe: 'Une pulsation marque un rapport des 30 derniers jours.',
    activeLabel: 'Flambées actives',
  },
};
