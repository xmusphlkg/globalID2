import type { Catalog, Language, ResultLine } from "../domain/types.ts";
const words = {
  title: ["Surveillance trends", "监测趋势", "Tendances de surveillance"],
  reportLatest: [
    "Latest published data, limited to this report's period; not a historical snapshot.",
    "使用最新发布数据并限定在本报告时段；不是历史快照。",
    "Données publiées les plus récentes, limitées à la période du rapport ; pas un instantané historique.",
  ],
  reportSnapshot: [
    "Observations frozen with this report; later data updates do not change this curve.",
    "报告发布时固化的观察值；后续数据更新不会改变本曲线。",
    "Observations figées avec ce rapport ; les mises à jour ultérieures ne modifient pas cette courbe.",
  ],
  reportSnapshotComparison: [
    "This report's observations are frozen; the current published series is shown separately for comparison.",
    "报告观察值已固化；当前发布序列另列用于对照。",
    "Les observations du rapport sont figées ; la série actuellement publiée est présentée séparément pour comparaison.",
  ],
  partial_report_window: [
    "This reporting period extends beyond the report window; its total is not treated as complete.",
    "该报告期间跨出本报告窗口，因此不作为完整期间合计。",
    "Cette période dépasse la fenêtre du rapport ; son total n'est pas considéré comme complet.",
  ],
  period_scope_mismatch: [
    "The archived value covers only part of this period; the current value covers the full period, so their difference is not a like-for-like revision.",
    "归档值只覆盖该期间的一部分，而当前值覆盖完整期间；两者差异不能直接解释为修订变化。",
    "La valeur archivée ne couvre qu’une partie de la période, tandis que la valeur actuelle la couvre entièrement ; leur différence n’est pas une révision comparable.",
  ],
  sourceVersion: [
    "Archived data version",
    "归档数据版本",
    "Version des données archivées",
  ],
  question: ["Analysis", "分析目标", "Analyse"],
  aggregateSelected: [
    "Sum selected regions",
    "汇总所选地区",
    "Agréger les régions sélectionnées",
  ],
  removeAggregate: [
    "Remove region aggregate",
    "取消地区汇总",
    "Supprimer l’agrégat régional",
  ],
  aggregateLine: [
    "Selected regions total",
    "所选地区合计",
    "Total des régions sélectionnées",
  ],
  aggregateMethod: [
    "Geographic sums require disjoint regions, matching count definitions and aligned periods. A period is shown only when every selected region has a valid observation; incomplete periods remain blank.",
    "地区汇总要求地理范围互不重叠、病例定义及期间口径一致。只有所有所选地区在同一期间都有有效观察值时才显示合计；不完整期间留空。",
    "Les sommes géographiques exigent des zones disjointes, des définitions de comptage identiques et des périodes alignées. Une valeur n’est affichée que si chaque région possède une observation valide ; les périodes incomplètes restent vides.",
  ],
  trend: ["Time trend", "时间变化", "Évolution temporelle"],
  burden: [
    "Compare reported burden",
    "比较报告负担",
    "Comparer les notifications",
  ],
  shape: ["Compare curve shapes", "比较变化形态", "Comparer les profils"],
  historical: [
    "Relative to historical seasons",
    "相对历史同期",
    "Par rapport aux saisons passées",
  ],
  change: ["Period change", "期间变化", "Variation entre périodes"],
  changeBasis: ["Compare with", "比较基期", "Comparer à"],
  changePrevious: ["Previous period", "上一期", "Période précédente"],
  changeYear: ["Same period last year", "上年同期", "Même période l’an dernier"],
  change_percent: ["Change (%)", "变化幅度（%）", "Variation (%)"],
  baseline: ["Baseline", "基期", "Référence"],
  changeMethod: [
    "Percentage change uses matching source periods: (current − baseline) / baseline × 100. Missing, zero or negative baselines and definition breaks have no result; year comparison requires a Gregorian calendar.",
    "百分比变化比较匹配的来源期间：（本期－基期）／基期 × 100。基期缺失、为零或负值，以及定义断点，均不计算；同比要求公历期间。",
    "La variation compare des périodes source correspondantes : (valeur actuelle − référence) / référence × 100. Aucune valeur si la référence manque, est nulle ou négative, ou si une définition change ; la comparaison annuelle exige un calendrier grégorien.",
  ],
  change_baseline_missing: [
    "Matching baseline period is unavailable.",
    "缺少可匹配的基期。",
    "La période de référence correspondante est indisponible.",
  ],
  change_baseline_nonpositive: [
    "Baseline is zero or negative; percentage change is undefined.",
    "基期为零或负值，百分比变化无定义。",
    "La référence est nulle ou négative ; la variation en pourcentage est indéfinie.",
  ],
  change_definition_break: [
    "A definition or source break separates these periods.",
    "两期间存在定义或来源断点。",
    "Une rupture de définition ou de source sépare ces périodes.",
  ],
  change_calendar_unknown: [
    "The calendar does not support this period comparison.",
    "来源日历不足以支持该期间比较。",
    "Le calendrier ne permet pas cette comparaison de périodes.",
  ],
  seasonality: [
    "Compare years / seasonality",
    "年际季节性",
    "Saisonnalité entre années",
  ],
  outbreak: [
    "Onset-time outbreak curve",
    "发病时间暴发曲线",
    "Courbe par date de début",
  ],
  sources: ["Compare sources", "核对不同来源", "Comparer les sources"],
  cases: ["Reported cases", "报告病例", "Cas déclarés"],
  deaths: ["Reported deaths", "报告死亡", "Décès déclarés"],
  incidence_rates: [
    "Cases per 100k per source period",
    "每报告期间每10万人病例数",
    "Cas pour 100 000 par période source",
  ],
  source_value: [
    "Source-defined measure",
    "来源定义指标",
    "Mesure définie par la source",
  ],
  count: [
    "Count per reporting period",
    "每报告期间数量",
    "Nombre par période déclarée",
  ],
  per_100k_source_period: [
    "Per 100k per source period",
    "每报告期间每10万人",
    "Pour 100 000 par période source",
  ],
  peak_index: [
    "Series peak = 100",
    "各序列全程峰值 = 100",
    "Pic de la série = 100",
  ],
  historical_index: [
    "Observed / historical median (%)",
    "观察值 / 历史同期中位数（%）",
    "Observé / médiane historique (%)",
  ],
  source_unit: ["Source unit", "来源单位", "Unité source"],
  inferred_population: [
    "The denominator is reconstructed from this location’s published computed rates; rounding can affect precision.",
    "人口分母由本地区已发布的计算率反推，舍入可能影响精度。",
    "Le dénominateur est reconstitué à partir des taux calculés publiés pour ce lieu ; l’arrondi peut affecter la précision.",
  ],
  subdivisions: ["Subdivisions", "省级地区", "Subdivisions"],
  definition_change: [
    "Definition change",
    "定义变更",
    "Changement de définition",
  ],
  source_change: ["Source change", "来源变更", "Changement de source"],
  daily: ["Daily", "日度", "Quotidien"],
  weekly: ["Weekly", "周度", "Hebdomadaire"],
  monthly: ["Monthly", "月度", "Mensuel"],
  quarterly: ["Quarterly", "季度", "Trimestriel"],
  annual: ["Annual", "年度", "Annuel"],
  unknown: ["Unspecified", "未明确", "Non précisé"],
  cadenceEstimate: ["inferred", "推断", "estimé"],
  cadenceEstimateHelp: [
    "Frequency inferred from the observation dates; source reporting period is not declared.",
    "频率依据观察日期推断，来源未声明报告期间。",
    "Fréquence déduite des dates ; la période de déclaration n’est pas précisée par la source.",
  ],
  metric: ["Measure", "指标", "Mesure"],
  both: [
    "Cases + deaths (separate panels)",
    "病例与死亡（独立面板）",
    "Cas et décès (panneaux séparés)",
  ],
  time: ["Time range", "时间范围", "Période"],
  all: ["All data", "全部数据", "Toutes les données"],
  start: ["Start date", "开始日期", "Date de début"],
  end: ["End date", "结束日期", "Date de fin"],
  objects: ["Choose series", "选择对象与来源", "Choisir les séries"],
  search: [
    "Search names or codes",
    "搜索名称或编码",
    "Rechercher un nom ou un code",
  ],
  scope: ["Candidate locations", "候选地区范围", "Lieux proposés"],
  national: ["Countries / regions", "国家／地区", "Pays et régions"],
  mixed: ["All locations", "所有地区", "Tous les lieux"],
  selected: ["Selected", "已选", "Sélection"],
  clear: ["Clear selection", "清空选择", "Effacer la sélection"],
  selectFiltered: [
    "Select filtered defaults",
    "选择筛选结果的默认序列",
    "Choisir les séries par défaut filtrées",
  ],
  published: ["Published default", "发布默认序列", "Série publiée par défaut"],
  sourceOptions: ["Source options", "查看来源", "Options de source"],
  retry: ["Retry", "重试", "Réessayer"],
  loading: ["Loading data…", "正在加载数据…", "Chargement des données…"],
  loadError: [
    "Data could not be loaded.",
    "数据加载失败。",
    "Échec du chargement des données.",
  ],
  noData: [
    "No observations for this selection.",
    "当前选择没有可用观察值。",
    "Aucune observation pour cette sélection.",
  ],
  noSelection: [
    "Choose a series to view data.",
    "选择一条序列查看数据。",
    "Choisissez une série pour afficher les données.",
  ],
  advanced: ["Edit query", "编辑查询", "Modifier la recherche"],
  aggregation: ["Reporting periods", "报告期间处理", "Périodes déclarées"],
  native: [
    "Keep source periods",
    "保留来源期间",
    "Conserver les périodes source",
  ],
  annualTotal: [
    "Complete calendar years",
    "完整自然年汇总",
    "Années civiles complètes",
  ],
  layout: ["Layout", "展示方式", "Disposition"],
  auto: ["Automatic", "自动", "Automatique"],
  overlay: ["Overlay", "叠加", "Superposition"],
  facets: ["Separate panels", "独立面板", "Panneaux séparés"],
  scale: ["Value axis", "数值坐标", "Axe des valeurs"],
  linear: ["Linear", "线性", "Linéaire"],
  log: ["Logarithmic", "对数", "Logarithmique"],
  smooth: ["Trailing average", "向后移动平均", "Moyenne mobile passée"],
  raw: ["Original values", "原始值", "Valeurs originales"],
  reference: ["Historical reference", "历史参照", "Référence historique"],
  referenceYears: [
    "Reference lookback (years)",
    "历史回溯年数",
    "Années de référence",
  ],
  common: ["Common coverage", "共同覆盖时间", "Couverture commune"],
  independent: [
    "Each series’ full coverage",
    "各序列自身覆盖",
    "Couverture propre à chaque série",
  ],
  compareWindow: ["Comparison window", "比较时间窗", "Fenêtre de comparaison"],
  undo: ["Undo", "撤销", "Annuler"],
  reset: [
    "Restore entry defaults",
    "恢复入口默认",
    "Rétablir les paramètres initiaux",
  ],
  resetTime: [
    "Show available time range",
    "显示有效时间范围",
    "Afficher la période disponible",
  ],
  draft: [
    "Review configuration changes",
    "检查配置变更",
    "Examiner les changements",
  ],
  apply: ["Apply configuration", "应用配置", "Appliquer"],
  cancel: ["Cancel changes", "取消更改", "Abandonner"],
  draftNote: [
    "The current result is retained. Review affected series before applying.",
    "当前结果已保留。请查看受影响序列，再应用配置。",
    "Le résultat actuel est conservé. Vérifiez les séries concernées avant d’appliquer.",
  ],
  chart: ["Chart", "图表", "Graphique"],
  table: ["Data", "数据", "Données"],
  methods: ["Methods & sources", "方法与来源", "Méthodes et sources"],
  quality: ["Quality & coverage", "质量与覆盖", "Qualité et couverture"],
  summary: ["Current window summary", "当前窗口摘要", "Résumé de la période"],
  latest: ["Latest observation", "最新有效观察值", "Dernière observation"],
  total: [
    "Complete-window total",
    "完整窗口总量",
    "Total de la période complète",
  ],
  observed: ["Observed periods", "有效期间", "Périodes observées"],
  missing: ["Missing periods", "缺失期间", "Périodes manquantes"],
  previous: [
    "Change from previous period",
    "较上一期变化",
    "Variation sur une période",
  ],
  yearChange: [
    "Change from matching prior year",
    "较上年同期变化",
    "Variation annuelle",
  ],
  name: ["Series", "序列", "Série"],
  date: ["Source date", "来源日期", "Date source"],
  value: ["Value", "数值", "Valeur"],
  unit: ["Unit", "单位", "Unité"],
  status: ["Quality", "质量状态", "Qualité"],
  hide: ["Hide curve", "隐藏曲线", "Masquer la courbe"],
  show: ["Show curve", "显示曲线", "Afficher la courbe"],
  remove: ["Remove series", "移除序列", "Retirer la série"],
  fullscreen: ["Enter full-screen", "进入全屏", "Plein écran"],
  exitFullscreen: ["Exit full-screen", "退出全屏", "Quitter le plein écran"],
  share: ["Share configuration", "分享配置", "Partager la configuration"],
  copied: [
    "Link copied; recalculates with the latest available data.",
    "链接已复制，将按最新可用数据重新计算。",
    "Lien copié ; recalcul avec les données disponibles les plus récentes.",
  ],
  shareManual: ["Copy this link", "复制此链接", "Copier ce lien"],
  exportCsv: [
    "Download data CSV",
    "下载数据 CSV",
    "Télécharger les données CSV",
  ],
  exportAnalysis: [
    "Download analysis & provenance",
    "下载分析与来源记录",
    "Télécharger l’analyse et la provenance",
  ],
  exportRawCsv: ["Download original data CSV", "下载原始数据 CSV", "Télécharger le CSV des données originales"],
  dataScope: ["Table and CSV data", "表格与 CSV 数据范围", "Données du tableau et du CSV"],
  currentWindow: ["Current window", "当前窗口", "Période affichée"],
  allDerived: [
    "All derived values",
    "全部派生数据",
    "Toutes les valeurs dérivées",
  ],
  rawData: ["Original observations", "原始观察值", "Observations originales"],
  next: ["Next", "下一页", "Suivant"],
  back: ["Previous", "上一页", "Précédent"],
  version: ["Data fingerprint", "数据指纹", "Empreinte des données"],
  recordedAtAsOf: ["Surveillance recorded as of", "监测数据系统记录截止时间", "Données de surveillance enregistrées au"],
  recordedSnapshotNote: ["This is an ingestion-time reconstruction, not the source publication time.", "此快照按本系统的采集记录时间重建，并非来源的发布时间。", "Cette reconstitution utilise l’heure d’enregistrement du système, pas la date de publication de la source."],
  recordedHistoryIncomplete: ["Some earlier values cannot be reconstructed because the revision archive is incomplete.", "由于修订历史不完整，部分历史值无法重建。", "Certaines valeurs antérieures ne peuvent pas être reconstituées car l’historique des révisions est incomplet."],
  snapshot: ["Report-scoped data", "报告范围数据", "Données du rapport"],
  referenceMethod: [
    "Reference: median and interquartile range of earlier matching seasons, at least 5 observations; provisional values are excluded from the baseline.",
    "历史参照：此前同期中位数与四分位范围，至少5个有效历史观察值；暂定值不进入基线。",
    "Référence : médiane et intervalle interquartile des saisons précédentes, au moins 5 observations ; valeurs provisoires exclues.",
  ],
  shapeMethod: [
    "Each series uses its full analyzed history for peak normalization; zooming does not change the baseline.",
    "各序列按完整分析历史的峰值归一化；缩放不会改变基准。",
    "Chaque série est normalisée par son pic sur toute la période analysée ; le zoom ne change pas la référence.",
  ],
  ratesMethod: [
    "Rates retain their source reporting period and denominator provenance; they are not annualized.",
    "率保留来源报告期间和分母来源，不作年化。",
    "Les taux conservent la période source et la provenance du dénominateur, sans annualisation.",
  ],
  latestNote: [
    "Latest dates can differ between series. Totals require complete, additive reporting periods.",
    "各序列最新有效日期可能不同。总量仅在可加且期间完整时显示。",
    "Les dates les plus récentes peuvent différer. Les totaux exigent des périodes complètes et additives.",
  ],
  invalidShare: [
    "This saved configuration does not match the available series or this page.",
    "保存的配置与当前页面或可用序列不匹配。",
    "Cette configuration ne correspond pas aux séries disponibles ou à cette page.",
  ],
  noSources: [
    "No additional registered sources",
    "没有额外注册来源",
    "Aucune source enregistrée supplémentaire",
  ],
  unavailable: [
    "Unavailable for selected data",
    "所选数据不支持",
    "Indisponible pour ces données",
  ],
  duplicate_period: [
    "Duplicate reporting periods are excluded from analysis.",
    "重复报告期间已从分析数值中排除。",
    "Les périodes déclarées en double sont exclues.",
  ],
  not_additive: [
    "This measure cannot be summed across periods.",
    "该指标不能跨期间求和。",
    "Cette mesure ne peut pas être additionnée entre périodes.",
  ],
  incomplete_year: [
    "Incomplete calendar year excluded",
    "已排除不完整自然年",
    "Année civile incomplète exclue",
  ],
  no_complete_year: [
    "No complete calendar year is available; inspect source periods.",
    "没有完整自然年；可查看来源期间数据。",
    "Aucune année civile complète ; consultez les périodes source.",
  ],
  history_calendar_unknown: [
    "Historical alignment requires a known source calendar.",
    "历史同期对齐需要明确的来源日历。",
    "L’alignement historique nécessite un calendrier source connu.",
  ],
  measure_unavailable: [
    "The selected measure is unavailable for this series.",
    "该序列没有所选指标。",
    "La mesure choisie est indisponible pour cette série.",
  ],
  unavailableOption: ["No data", "暂无数据", "Aucune donnée"],
  source_unloaded: [
    "Source observations need to be loaded.",
    "需要加载该来源的观察值。",
    "Les observations de la source doivent être chargées.",
  ],
  no_observations: [
    "No valid observations.",
    "没有有效观察值。",
    "Aucune observation valide.",
  ],
  outbreak_ineligible: [
    "Requires daily or weekly observations explicitly recorded by symptom onset.",
    "需要明确按发病时间统计的日度或周度观察值。",
    "Des observations quotidiennes ou hebdomadaires par début des symptômes sont requises.",
  ],
  normalization_ineligible: [
    "Peak normalization requires a positive peak and nonnegative values.",
    "峰值归一化需要正峰值且数值非负。",
    "La normalisation exige un pic positif et des valeurs non négatives.",
  ],
  history_insufficient: [
    "Insufficient comparable historical seasons; original data remain available.",
    "可比历史同期不足；仍可查看原始数据。",
    "Historique comparable insuffisant ; les données originales restent disponibles.",
  ],
  seasonality_ineligible: [
    "Seasonal alignment needs daily, exact ISO-week, monthly or quarterly calendar data.",
    "季节性对齐需要日度、精确 ISO 周度、月度或季度日历数据。",
    "L’alignement saisonnier exige des données quotidiennes, hebdomadaires ISO exactes, mensuelles ou trimestrielles.",
  ],
  seasonality_calendar_unknown: [
    "Seasonal alignment needs known calendar boundaries; this series remains available as a regular trend.",
    "季节性对齐需要明确的日历期间边界；该序列仍可作为普通趋势查看。",
    "L’alignement saisonnier exige des limites de calendrier connues ; cette série reste disponible comme tendance ordinaire.",
  ],
  cadence_unknown: [
    "Reporting frequency is unspecified; inspect source documentation.",
    "报告频率未明确，请查看来源说明。",
    "Fréquence non précisée ; consultez la source.",
  ],
  provisional: [
    "Provisional observations may be revised.",
    "暂定观察值可能修订。",
    "Les observations provisoires peuvent être révisées.",
  ],
  no_common_window: [
    "No common observed window; series are shown independently.",
    "没有共同观测时间窗，已独立展示各序列。",
    "Aucune période commune ; les séries sont présentées séparément.",
  ],
  not_comparable: [
    "A source forbids direct comparison; independent panels preserve its observations.",
    "来源明确不支持直接比较，已用独立面板保留观察值。",
    "Une source interdit la comparaison directe ; ses observations sont conservées séparément.",
  ],
  conditional_comparison: [
    "Comparability is conditional or not established. Check definitions and reporting dates.",
    "可比性有条件或尚未确认，请核对定义与报告日期。",
    "Comparabilité conditionnelle ou non établie. Vérifiez les définitions et dates.",
  ],
  separate_panels: [
    "Units, reporting frequencies or definitions differ; panels are separated.",
    "单位、报告频率或定义不同，已分面展示。",
    "Les unités, fréquences ou définitions diffèrent ; panneaux séparés.",
  ],
  overlay_unavailable: [
    "Overlay is unavailable for this combination; separate panels are used.",
    "当前组合不支持叠加，采用独立面板。",
    "Superposition indisponible ; panneaux séparés.",
  ],
  empty_window: [
    "No observations in this time range. Restore the available range.",
    "当前时间范围没有观察值，可恢复有效范围。",
    "Aucune observation dans cette période. Rétablissez la période disponible.",
  ],
  log_nonpositive: [
    "Zero or negative values require a linear axis; all observations are retained.",
    "包含零或负值，使用线性坐标并保留全部观察值。",
    "Des valeurs nulles ou négatives nécessitent un axe linéaire ; toutes sont conservées.",
  ],
  aggregate_method_unsupported: [
    "Region sums are available only for reported case or death counts without smoothing.",
    "地区汇总仅支持未平滑的报告病例数或死亡数。",
    "Les sommes régionales ne sont disponibles que pour les cas ou décès déclarés sans lissage.",
  ],
  aggregate_geography_overlap: [
    "Selected locations overlap or have unknown administrative boundaries; they were not summed.",
    "所选地区存在父子重叠或行政层级未知，未执行汇总。",
    "Les zones sélectionnées se chevauchent ou leurs limites administratives sont inconnues ; elles n’ont pas été agrégées.",
  ],
  aggregate_member_unavailable: [
    "Every selected region must have this measure available before it can be summed.",
    "汇总前，每个所选地区都必须有该指标。",
    "Chaque région sélectionnée doit disposer de cette mesure pour être agrégée.",
  ],
  aggregate_incompatible: [
    "Selected regions do not share a directly comparable count definition, unit, cadence or exact period boundaries.",
    "所选地区的病例定义、单位、频率或期间边界不一致，无法直接汇总。",
    "Les régions sélectionnées n’ont pas de définition de comptage, d’unité, de fréquence ou de limites de période directement comparables.",
  ],
  aggregate_incomplete: [
    "At least one region is missing or has a partial observation in this period; the aggregate is blank.",
    "至少一个地区在该期间缺失或仅有部分观察值，因此合计留空。",
    "Au moins une région manque de données ou ne dispose que d’une observation partielle pour cette période ; l’agrégat reste vide.",
  ],
} satisfies Record<string, readonly [string, string, string]>;
export type LabelKey = keyof typeof words;
export function label(key: string, lang: Language): string {
  return (
    words[key as LabelKey]?.[lang === "zh" ? 1 : lang === "fr" ? 2 : 0] ?? key
  );
}
export function lineName(
  line: ResultLine,
  catalog: Catalog,
  lang: Language,
): string {
  if (line.aggregateMembers?.length)
    return `${label("aggregateLine", lang)} (${line.aggregateMembers.length}) · ${label(line.measure, lang)}`;
  const selection = catalog.selections[line.selectionId];
  return [
    catalog.entities[line.entityId]?.labels[lang] ?? line.entityId,
    selection?.sourceLabel,
    label(line.measure, lang),
    line.labelSuffix,
  ]
    .filter(Boolean)
    .join(" · ");
}
export function number(
  value: number | null | undefined,
  lang: Language,
): string {
  return value == null || !Number.isFinite(value)
    ? "—"
    : value.toLocaleString(lang, { maximumFractionDigits: 2 });
}
