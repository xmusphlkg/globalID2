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

type DiseaseNames = Pick<SituationAlertRecord, 'disease_label' | 'disease_name_en' | 'disease_name_zh'>;
type CountryNames = Pick<SituationAlertRecord, 'country_code' | 'country_name_en' | 'country_name_zh'>;
type SourceNames = Pick<SituationAlertRecord, 'source_id' | 'source_label_en' | 'source_label_zh'>;

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
  mapLabel: string;
  updated: string;
  firstReported: string;
  alertsCount: (n: number) => string;
  sourceLine: string;
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
    legendNote: 'Colour shows how recent the report is. It does not show how severe the event is.',
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
    mapLabel: 'World map of places with recent outbreak reports',
    updated: 'Updated',
    firstReported: 'First reported',
    alertsCount: n => `${n} ${n === 1 ? 'alert' : 'alerts'}`,
    sourceLine: 'Source',
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
    legendNote: '颜色只表示报告有多新，不表示事件的严重程度。',
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
    mapLabel: '近期有疫情报告的地点世界地图',
    updated: '更新于',
    firstReported: '首次报告',
    alertsCount: n => `${n} 条警报`,
    sourceLine: '来源',
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
    legendNote: 'La couleur indique la fraîcheur du rapport, pas la gravité de l’événement.',
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
    mapLabel: 'Carte du monde des lieux avec des rapports récents de flambée',
    updated: 'Mis à jour',
    firstReported: 'Premier signalement',
    alertsCount: n => `${n} ${n === 1 ? 'alerte' : 'alertes'}`,
    sourceLine: 'Source',
  },
};
