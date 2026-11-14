/** Map-stage copy; compact notice formatting lives in its own client module. */
import type { Locale } from '../../utils/situationTypes.ts';
export { formatDay, formatCount, formatSignedPercent, diseaseName, countryName, sourceLabel, alertSummary, STATUS_LABELS, basisLabel, countsLine } from './noticeLabels';

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
  fresh: string;
  recent: string;
  older: string;
  stale: string;
  detailHeading: string;
  otherCountries: string;
  showEarlier: (shown: number, total: number) => string;
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
  dataFailed: string;
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
  seriesLegend: string;
  monitoringHeading: string;
  monitoringLead: string;
  close: string;
  activeLabel: string;
  statCases: string;
  statDeaths: string;
  statAsOf: string;
}

export const COPY: Record<Locale, SituationCopy> = {
  en: {
    filters: 'Filters',
    disease: 'Disease',
    country: 'Area',
    source: 'Source',
    window: 'Time window',
    any: 'All',
    reset: 'Reset',
    win7: 'Last 7 days',
    win30: 'Last 30 days',
    win90: 'Last 90 days',
    winAll: 'Whole period',
    listHeading: 'Places with news',
    noResults: 'No place matches these filters.',
    places: n => `${n} ${n === 1 ? 'place' : 'places'}`,
    fresh: '≤ 7 days',
    recent: '8–30 days',
    older: '31–90 days',
    stale: '> 90 days',
    detailHeading: 'Alert timeline',
    otherCountries: 'Other areas',
    showEarlier: (shown, total) => `Show earlier alerts (${shown} of ${total} shown)`,
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
    dataFailed: 'The situation markers could not be loaded. Refresh the page to try again.',
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
    linked: (linked, total) => `${linked} of ${total} places match a top-level area series`,
    countryDossier: 'Open this area',
    alertsHere: 'Alerts in this area',
    noAlertsHere: 'No alert in this window.',
    widenWindow: 'Show the whole period',
    surveillanceHeading: 'GIDS surveillance',
    nationalSeries: 'Top-level area surveillance series',
    noNationalSeries: 'GIDS does not publish a top-level area surveillance series for this place.',
    diseaseNotInSeries: (disease, count) => `${disease} is not in the top-level area series. GIDS tracks ${count} diseases here.`,
    trackedDiseases: n => `Tracks ${n} ${n === 1 ? 'disease' : 'diseases'}`,
    openCountry: 'Area surveillance',
    openDisease: 'Disease page',
    openResearch: n => `${n} ${n === 1 ? 'paper' : 'papers'}`,
    vsPrevious: 'vs previous period',
    seriesLegend: 'Shading: latest GIDS value for the selected disease.',
    monitoringHeading: 'Monitoring products',
    monitoringLead: 'Periodic roundups with nothing unusual. They add no point.',
    close: 'Close',
    activeLabel: 'Active outbreaks',
    statCases: 'Cases',
    statDeaths: 'Deaths',
    statAsOf: 'As of',
  },
  zh: {
    filters: '筛选',
    disease: '疾病',
    country: '区域',
    source: '来源',
    window: '时间范围',
    any: '全部',
    reset: '重置',
    win7: '最近 7 天',
    win30: '最近 30 天',
    win90: '最近 90 天',
    winAll: '全部时段',
    listHeading: '有新消息的地点',
    noResults: '没有符合筛选条件的地点。',
    places: n => `${n} 个地点`,
    fresh: '7 天内',
    recent: '8–30 天',
    older: '31–90 天',
    stale: '90 天以上',
    detailHeading: '警报时间线',
    otherCountries: '其他区域',
    showEarlier: (shown, total) => `显示更早的警报（已显示 ${shown} / ${total} 条）`,
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
    dataFailed: '态势点位无法加载。请刷新页面重试。',
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
    linked: (linked, total) => `${total} 个地点中有 ${linked} 个能对上上级区域序列`,
    countryDossier: '打开该国',
    alertsHere: '该国的警报',
    noAlertsHere: '当前时间范围内没有警报。',
    widenWindow: '显示全部时段',
    surveillanceHeading: '本站监测',
    nationalSeries: '上级区域监测序列',
    noNationalSeries: 'GIDS 没有发布该地的上级区域监测序列。',
    diseaseNotInSeries: (disease, count) => `上级区域序列未收录${disease}。GIDS 在这里跟踪 ${count} 种疾病。`,
    trackedDiseases: n => `跟踪 ${n} 种疾病`,
    openCountry: '区域监测页',
    openDisease: '疾病页',
    openResearch: n => `${n} 篇研究`,
    vsPrevious: '较上一周期',
    seriesLegend: '底色：所选疾病在 GIDS 中的最新值。',
    monitoringHeading: '监测产品',
    monitoringLead: '没有异常事件的定期汇总，不会在地图上增加点。',
    close: '关闭',
    activeLabel: '活跃疫情',
    statCases: '病例',
    statDeaths: '死亡',
    statAsOf: '截至',
  },
  fr: {
    filters: 'Filtres',
    disease: 'Maladie',
    country: 'Zone',
    source: 'Source',
    window: 'Période',
    any: 'Tous',
    reset: 'Réinit.',
    win7: '7 derniers jours',
    win30: '30 derniers jours',
    win90: '90 derniers jours',
    winAll: 'Toute la période',
    listHeading: 'Lieux avec des nouvelles',
    noResults: 'Aucun lieu ne correspond à ces filtres.',
    places: n => `${n} ${n === 1 ? 'lieu' : 'lieux'}`,
    fresh: '≤ 7 jours',
    recent: '8–30 jours',
    older: '31–90 jours',
    stale: '> 90 jours',
    detailHeading: 'Chronologie des alertes',
    otherCountries: 'Autres zones',
    showEarlier: (shown, total) => `Afficher les alertes plus anciennes (${shown} sur ${total})`,
    pickHint: 'Sélectionnez un lieu sur la carte ou dans la liste pour voir les alertes de cette flambée.',
    originalReport: 'Rapport original',
    quote: 'Extrait du rapport',
    quoteWithheld: 'Aucune citation affichée : cette source n’autorise pas la réutilisation. Consultez le rapport original.',
    noCounts: 'Le rapport ne donne ni cas ni décès pour ce lieu.',
    localHeading: 'Série de surveillance locale',
    localLatest: 'Dernière valeur de la série GIDS',
    localCurve: 'Ouvrir la courbe zone-maladie',
    placesInOutbreak: 'Lieux de cette flambée',
    unplaced: 'Non placés sur la carte',
    mapLoading: 'Chargement de la carte…',
    mapFailed: 'La carte n’a pas pu être chargée. La liste et la chronologie restent disponibles.',
    dataFailed: 'Les marqueurs de situation n’ont pas pu être chargés. Actualisez la page pour réessayer.',
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
    linked: (linked, total) => `${linked} lieux sur ${total} correspondent à une série de zone de niveau supérieur`,
    countryDossier: 'Ouvrir cette zone',
    alertsHere: 'Alertes dans cette zone',
    noAlertsHere: 'Aucune alerte sur cette période.',
    widenWindow: 'Afficher toute la période',
    surveillanceHeading: 'Surveillance GIDS',
    nationalSeries: 'Série de surveillance de zone de niveau supérieur',
    noNationalSeries: 'GIDS ne publie pas de série de surveillance de zone de niveau supérieur pour ce lieu.',
    diseaseNotInSeries: (disease, count) => `${disease} ne figure pas dans la série de zone de niveau supérieur. GIDS suit ${count} maladies ici.`,
    trackedDiseases: n => `Suit ${n} ${n === 1 ? 'maladie' : 'maladies'}`,
    openCountry: 'Surveillance de la zone',
    openDisease: 'Page de la maladie',
    openResearch: n => `${n} ${n === 1 ? 'article' : 'articles'}`,
    vsPrevious: 'vs période précédente',
    seriesLegend: 'Teinte : dernière valeur GIDS pour la maladie choisie.',
    monitoringHeading: 'Produits de surveillance',
    monitoringLead: 'Synthèses périodiques sans événement inhabituel. Elles n’ajoutent aucun point.',
    close: 'Fermer',
    activeLabel: 'Flambées actives',
    statCases: 'Cas',
    statDeaths: 'Décès',
    statAsOf: 'Au',
  },
};
