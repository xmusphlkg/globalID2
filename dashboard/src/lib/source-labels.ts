import type { CountrySourceConfig, SourceOption } from "@/lib/hooks/useSources";

export type DashboardLang = "en" | "zh";

const SOUTH_ASIA_SOURCE_LABELS: Record<string, { scope: string; en: string; zh: string }> = {
  "BD": {
    "scope": "bd_dghs_dengue_daily",
    "en": "Bangladesh DGHS Daily Dengue Surveillance",
    "zh": "孟加拉国 DGHS 登革热每日住院与死亡监测"
  },
  "BT": {
    "scope": "bt_rcdc_publications",
    "en": "Bhutan RCDC Publications and Sentinel Surveillance",
    "zh": "不丹 RCDC 出版物与哨点监测"
  },
  "MV": {
    "scope": "mv_hpa_communicable",
    "en": "Maldives HPA Communicable Disease Reports",
    "zh": "马尔代夫 HPA 传染病报告"
  },
  "AF": {
    "scope": "af_ndsr_weekly",
    "en": "Afghanistan MoPH NDSR Weekly Epidemiological Report",
    "zh": "阿富汗卫生部 NDSR 流行病学周报"
  }
};

Object.assign(SOUTH_ASIA_SOURCE_LABELS, {
  "IN": {
    "scope": "in_idsp_weekly",
    "en": "India NCDC IDSP Weekly Outbreak Reports",
    "zh": "印度 IDSP 疫情暴发周报"
  },
  "PK": {
    "scope": "pk_nih_idsr_weekly",
    "en": "Pakistan NIH IDSR Weekly Bulletin",
    "zh": "巴基斯坦 NIH IDSR 监测周报"
  },
  "NP": {
    "scope": "np_sormas_weekly",
    "en": "Nepal EDCD SORMAS Public Weekly Bulletin",
    "zh": "尼泊尔 EDCD SORMAS 公共监测周报"
  },
  "LK": {
    "scope": "lk_epid_wer_weekly",
    "en": "Sri Lanka Epidemiology Unit Weekly Epidemiological Report",
    "zh": "斯里兰卡流行病学处疾病周报"
  }
});

const ECDC_COUNTRY_LABELS: Record<string, { en: string; zh: string }> = {
  AT: { en: "Austria", zh: "奥地利" },
  BE: { en: "Belgium", zh: "比利时" },
  BG: { en: "Bulgaria", zh: "保加利亚" },
  HR: { en: "Croatia", zh: "克罗地亚" },
  CY: { en: "Cyprus", zh: "塞浦路斯" },
  DK: { en: "Denmark", zh: "丹麦" },
  EE: { en: "Estonia", zh: "爱沙尼亚" },
  FI: { en: "Finland", zh: "芬兰" },
  FR: { en: "France", zh: "法国" },
  GB: { en: "United Kingdom", zh: "英国" },
  DE: { en: "Germany", zh: "德国" },
  HU: { en: "Hungary", zh: "匈牙利" },
  IS: { en: "Iceland", zh: "冰岛" },
  IE: { en: "Ireland", zh: "爱尔兰" },
  ES: { en: "Spain", zh: "西班牙" },
  IT: { en: "Italy", zh: "意大利" },
  LV: { en: "Latvia", zh: "拉脱维亚" },
  LI: { en: "Liechtenstein", zh: "列支敦士登" },
  LT: { en: "Lithuania", zh: "立陶宛" },
  LU: { en: "Luxembourg", zh: "卢森堡" },
  MT: { en: "Malta", zh: "马耳他" },
  NL: { en: "Netherlands", zh: "荷兰" },
  NO: { en: "Norway", zh: "挪威" },
  PT: { en: "Portugal", zh: "葡萄牙" },
  PL: { en: "Poland", zh: "波兰" },
  CZ: { en: "Czechia", zh: "捷克" },
  GR: { en: "Greece", zh: "希腊" },
  RO: { en: "Romania", zh: "罗马尼亚" },
  SK: { en: "Slovakia", zh: "斯洛伐克" },
  SI: { en: "Slovenia", zh: "斯洛文尼亚" },
  SE: { en: "Sweden", zh: "瑞典" },
};

function getEcdcLabel(countryCode: string, lang: DashboardLang): string {
  const country = ECDC_COUNTRY_LABELS[countryCode];
  if (!country) {
    return lang === "zh" ? "ECDC 传染病监测图谱年度基线" : "ECDC Surveillance Atlas Annual Baseline";
  }
  return lang === "zh"
    ? `ECDC 传染病监测图谱（${country.zh}年度基线）`
    : `ECDC Surveillance Atlas — ${country.en} Annual Baseline`;
}

export function getSourceOptionLabel(option: SourceOption, lang: DashboardLang = "en"): string {
  return lang === "zh" ? option.label_zh || option.label : option.label_en || option.label;
}

export function getConfiguredSourceOptions(
  config?: CountrySourceConfig | null,
  lang: DashboardLang = "en",
  fallbackCountryCode?: string | null,
): Array<{ value: string; label: string }> {
  if (!config) {
    return getSourceOptionsForCountry(fallbackCountryCode || "", lang);
  }
  if (!config.source_options.length) {
    return getSourceOptionsForCountry(config.country_code, lang);
  }
  return config.source_options.map((option) => ({
    value: option.value,
    label: getSourceOptionLabel(option, lang),
  }));
}

const REMAINING_SOURCE_LABELS: Record<string, { en: string; zh: string; scope: string }> = {
  "ET": {
    "en": "Ethiopia WHO GHO Annual Reported Infections",
    "zh": "埃塞俄比亚 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "KE": {
    "en": "Kenya WHO GHO Annual Reported Infections",
    "zh": "肯尼亚 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "UG": {
    "en": "Uganda WHO GHO Annual Reported Infections",
    "zh": "乌干达 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "GH": {
    "en": "Ghana WHO GHO Annual Reported Infections",
    "zh": "加纳 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "TZ": {
    "en": "Tanzania WHO GHO Annual Reported Infections",
    "zh": "坦桑尼亚 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "PH": {
    "en": "Philippines WHO GHO Annual Reported Infections",
    "zh": "菲律宾 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "VN": {
    "en": "Viet Nam WHO GHO Annual Reported Infections",
    "zh": "越南 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "LA": {
    "en": "Lao PDR WHO GHO Annual Reported Infections",
    "zh": "老挝 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "MM": {
    "en": "Myanmar WHO GHO Annual Reported Infections",
    "zh": "缅甸 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "BN": {
    "en": "Brunei Darussalam WHO GHO Annual Reported Infections",
    "zh": "文莱 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "TL": {
    "en": "Timor-Leste WHO GHO Annual Reported Infections",
    "zh": "东帝汶 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "PY": {
    "en": "Paraguay WHO GHO Annual Reported Infections",
    "zh": "巴拉圭 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "EC": {
    "en": "Ecuador WHO GHO Annual Reported Infections",
    "zh": "厄瓜多尔 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "CR": {
    "en": "Costa Rica WHO GHO Annual Reported Infections",
    "zh": "哥斯达黎加 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "PA": {
    "en": "Panama WHO GHO Annual Reported Infections",
    "zh": "巴拿马 WHO GHO 传染病年度报告指标",
    "scope": "who_gho_annual"
  },
  "GP": {
    "en": "Guadeloupe SPF ODISSE Annual Notifications",
    "zh": "瓜德罗普 SPF ODISSÉ 年度疾病报告",
    "scope": "spf_odisse_annual"
  },
  "MQ": {
    "en": "Martinique SPF ODISSE Annual Notifications",
    "zh": "马提尼克 SPF ODISSÉ 年度疾病报告",
    "scope": "spf_odisse_annual"
  },
  "GF": {
    "en": "French Guiana SPF ODISSE Annual Notifications",
    "zh": "法属圭亚那 SPF ODISSÉ 年度疾病报告",
    "scope": "spf_odisse_annual"
  }
};

const ADDITIONAL_SOURCE_LABELS: Record<string, Record<string, {en: string; zh: string}>> = {
  "CR": {
    "cr_moh_notifications": {
      "en": "Costa Rica Ministry of Health Notification Reports",
      "zh": "哥斯达黎加卫生部疾病报告"
    }
  },
  "LA": {
    "who_wpro_dengue": {
      "en": "Lao PDR WHO WPRO Dengue Updates",
      "zh": "老挝 WHO 西太区登革热监测"
    }
  },
  "VN": {
    "who_wpro_dengue": {
      "en": "Viet Nam WHO WPRO Dengue Updates",
      "zh": "越南 WHO 西太区登革热监测"
    }
  }
};

export function getSourceDisplayLabel(
  source?: string | null,
  lang: DashboardLang = "en",
  countryCode?: string | null,
): string {
  const s = (source || "all").trim().toLowerCase();
  const cc = (countryCode || "").trim().toUpperCase();
  const south = Object.values(SOUTH_ASIA_SOURCE_LABELS).find(value => value.scope === s) || (s === "all" ? SOUTH_ASIA_SOURCE_LABELS[cc] : undefined);
  if (south) return south[lang === "zh" ? "zh" : "en"];
  const extra = ADDITIONAL_SOURCE_LABELS[cc]?.[s];
  if (extra) return extra[lang === "zh" ? "zh" : "en"];
  const remaining = REMAINING_SOURCE_LABELS[cc];
  if (remaining && (s === remaining.scope || s === "all")) return remaining[lang === "zh" ? "zh" : "en"];
  if (s === "cl_eno_historical" || (s === "all" && cc === "CL")) {
    return lang === "zh" ? "智利 MINSAL 法定报告疾病历史数据" : "Chile MINSAL ENO Historical Notifications";
  }
  if (s === "co_ins_sivigila_historical") {
    return lang === "zh" ? "哥伦比亚 INS SIVIGILA 历史监测数据" : "Colombia INS SIVIGILA Historical Public Health Surveillance";
  }
  if (s === "co_ins_sivigila_current") {
    return lang === "zh" ? "哥伦比亚 INS SIVIGILA 后续数据集（2023 年起）" : "Colombia INS SIVIGILA Successor Dataset (2023 onward)";
  }
  if (s === "co_ins_bes_weekly") {
    return lang === "zh" ? "哥伦比亚 INS BES 流行病学周报" : "Colombia INS BES Epidemiological Bulletins";
  }
  if (s === "mw_phim_idsr_weekly" || (s === "all" && cc === "MW")) {
    return lang === "zh" ? "马拉维 PHIM IDSR 疾病监测周报" : "Malawi PHIM IDSR Weekly Bulletin";
  }
  if (s === "za_nicd_nmc" || (s === "all" && cc === "ZA")) {
    return lang === "zh" ? "南非 NICD 法定传染病月报" : "South Africa NICD NMC";
  }
  if (s === "id_kemkes_profile" || (s === "all" && cc === "ID")) {
    return lang === "zh" ? "印度尼西亚卫生部年度卫生概况" : "Indonesia Ministry of Health Annual Health Profile";
  }
  if (s === "mx_dge_annual" || (s === "all" && cc === "MX")) {
    return lang === "zh" ? "墨西哥 DGE 年度发病统计" : "Mexico DGE Annual Morbidity";
  }
  if (s === "ncdc_surveillance" || (s === "all" && cc === "NG")) {
    return lang === "zh" ? "尼日利亚 NCDC 疾病监测" : "Nigeria NCDC Surveillance";
  }
  if (s === "kh_camewarn_weekly" || (s === "all" && cc === "KH")) {
    return lang === "zh" ? "柬埔寨 CamEWARN 疾病监测周报" : "Cambodia CDC CamEWARN Weekly Surveillance";
  }
  if (s === "ar_ben_weekly" || (s === "all" && cc === "AR")) {
    return lang === "zh" ? "阿根廷国家流行病学周报" : "Argentina National Epidemiological Bulletin (BEN)";
  }
  if (s === "pe_cdc_weekly" || (s === "all" && cc === "PE")) {
    return lang === "zh" ? "秘鲁 CDC 流行病学周报" : "Peru CDC Epidemiological Bulletin";
  }
  if (s === "th_dds_tableau") {
    return lang === "zh" ? "泰国 DDS Tableau 多病种全国周监测" : "Thailand DDS Tableau Weekly National Surveillance";
  }
  if (s === "th_id_th_history") {
    return lang === "zh" ? "泰国 ID_TH 历史周数据" : "Thailand ID_TH Weekly Archive";
  }
  if (s === "th_dds_weekly" || (s === "all" && cc === "TH")) {
    return lang === "zh" ? "泰国 DDS 多病种全国周报" : "Thailand DDS Weekly National Situation";
  }
  if (s === "th_doe506_monthly" || s === "doe506") {
    return lang === "zh" ? "泰国 DOE 506 多病种月度历史报表" : "Thailand DOE Report 506 Monthly History";
  }
  if (s === "my_idengue_daily") {
    return lang === "zh" ? "马来西亚 iDengue 登革热每日监测" : "Malaysia iDengue Daily Dengue Surveillance";
  }
  if (s === "who_flunet_weekly") {
    return lang === "zh" ? "马来西亚 WHO FluNet 呼吸道实验室周监测" : "Malaysia WHO FluNet Weekly Respiratory Laboratory Surveillance";
  }
  if (s === "who_gho_annual") {
    return lang === "zh" ? "马来西亚 WHO GHO 传染病年度报告指标" : "Malaysia WHO GHO Annual Reported Infections";
  }
  if (s === "rivm_virological_weekly") {
    return lang === "zh" ? "荷兰 RIVM 多病原实验室周监测" : "Netherlands RIVM Virological Weekly Surveillance";
  }
  if (s === "rivm_respiratory_weekly") {
    return lang === "zh" ? "荷兰 RIVM 呼吸道周监测" : "Netherlands RIVM Respiratory Weekly Surveillance";
  }
  if (s === "moh_std_annual" || s === "my_moh_std" || (s === "all" && cc === "MY")) {
    return lang === "zh" ? "马来西亚卫生部性传播疾病年度报表" : "Malaysia MOH Annual STD Notifications";
  }
  if (s === "rivm_notifiable_annual" || s === "rivm_notifiable" || (s === "all" && cc === "NL")) {
    return lang === "zh" ? "荷兰 RIVM 法定传染病年度报表" : "Netherlands RIVM Annual Notifiable Disease Notifications";
  }
  if (s === "nndss_api") return "US CDC NNDSS";
  if (
    s === "phac_cndss_annual" ||
    s === "phac_cndss" ||
    s === "cndss" ||
    ((s === "all" || s === "ca" || s === "canada") && cc === "CA")
  ) {
    return lang === "zh" ? "加拿大公共卫生署 CNDSS 年度数据" : "Canada PHAC CNDSS Annual";
  }
  if (s === "nhss_hiv" || s === "nhss" || s === "hiv_nhss") {
    return lang === "zh" ? "美国 CDC NHSS HIV 监测" : "US CDC NHSS HIV";
  }
  if (s === "jp_idwr" || s === "jp_weekly" || (s === "all" && cc === "JP")) return "JP NIID Weekly";
  if (s === "nidss_open_data" || s === "nidss" || ((s === "all" || s === "tw") && cc === "TW")) {
    return lang === "zh" ? "中国台湾 CDC NIDSS" : "Taiwan, China CDC NIDSS";
  }
  if (s === "chp_notifiable" || s === "chp" || s === "hk_chp" || ((s === "all" || s === "hk") && cc === "HK")) {
    return lang === "zh" ? "中国香港 CHP 法定传染病" : "Hong Kong, China CHP Notifiable Diseases";
  }
  if (s === "sinan_datasus" || s === "sinan" || s === "datasus" || ((s === "all" || s === "br") && cc === "BR")) {
    return lang === "zh" ? "巴西 DATASUS SINAN" : "Brazil DATASUS SINAN";
  }
  if (
    s === "kdca_open_api" ||
    s === "kdca" ||
    s === "kdca_dportal" ||
    s === "kdca_portal" ||
    s === "kosis" ||
    s === "korea kdca eid portal download" ||
    s === "korea kosis download" ||
    ((s === "all" || s === "kr" || s === "korea") && cc === "KR")
  ) {
    return lang === "zh" ? "韩国 KDCA EID" : "Korea KDCA EID";
  }
  if (
    s === "foph_idd" ||
    s === "foph" ||
    s === "bag" ||
    s === "bag_idd" ||
    s === "idd" ||
    ((s === "all" || s === "ch" || s === "switzerland") && cc === "CH")
  ) {
    return lang === "zh" ? "瑞士 FOPH/BAG IDD" : "Switzerland FOPH IDD";
  }
  if (
    s === "pho_idto_monthly" ||
    s === "pho_idto" ||
    s === "idto" ||
    (s === "ontario" && cc === "CA")
  ) {
    return lang === "zh" ? "安大略省公共卫生局 IDTO 月度数据" : "Public Health Ontario IDTO Monthly";
  }
  if (
    s === "phf_monthly" ||
    s === "phf" ||
    ((s === "all" || s === "nz" || s === "new_zealand") && cc === "NZ")
  ) {
    return lang === "zh" ? "新西兰 PHF Science 法定传染病月度监测" : "New Zealand PHF Science Monthly Notifiable Diseases";
  }
  if (
    s === "thl_ttr" ||
    s === "thl" ||
    s === "ttr" ||
    ((s === "all" || s === "fi" || s === "finland") && cc === "FI")
  ) {
    return lang === "zh" ? "芬兰 THL 传染病登记" : "Finland THL Infectious Diseases Register";
  }
  if (s === "ages_radar" || s === "ages" || ((s === "all" || s === "at" || s === "austria") && cc === "AT")) {
    return lang === "zh" ? "奥地利 AGES 传染病雷达" : "Austria AGES Radar for Infectious Diseases";
  }
  if (s === "rki_survstat" || s === "survstat" || ((s === "all" || s === "de" || s === "germany") && cc === "DE")) {
    return lang === "zh" ? "德国 RKI SurvStat 2.0" : "Germany RKI SurvStat 2.0";
  }
  if (
    s === "hpsc_weekly_archive" ||
    s === "hpsc_archive" ||
    s === "ie_weekly_archive"
  ) {
    return lang === "zh" ? "爱尔兰 HPSC 周报档案（2017–2021年第29周）" : "Ireland HPSC Weekly Report Archive (2017–2021 W29)";
  }
  if (
    s === "hpsc_annual" ||
    s === "hpsc_annual_history" ||
    s === "ie_annual"
  ) {
    return lang === "zh" ? "爱尔兰 HPSC 年度历史统计（2004–2020）" : "Ireland HPSC Annual Statistics (2004–2020)";
  }
  if (
    s === "hpsc_ndh" ||
    s === "hpsc" ||
    s === "ndh" ||
    ((s === "all" || s === "ie" || s === "ireland") && cc === "IE")
  ) {
    return lang === "zh" ? "爱尔兰 HPSC 法定传染病中心" : "Ireland HPSC Notifiable Diseases Hub";
  }
  if (
    s === "fhi_msis" ||
    s === "fhi" ||
    s === "msis" ||
    ((s === "all" || s === "no" || s === "norway") && cc === "NO")
  ) {
    return lang === "zh" ? "挪威 FHI MSIS 统计库" : "Norway FHI MSIS Statistics Bank";
  }
  if (
    s === "fohm_sminet" ||
    s === "fohm" ||
    s === "sminet" ||
    ((s === "all" || s === "se" || s === "sweden") && cc === "SE")
  ) {
    return lang === "zh" ? "瑞典公共卫生局 SmiNet" : "Sweden Public Health Agency SmiNet";
  }
  if (
    s === "cda_weekly_bulletin" ||
    s === "cda" ||
    s === "widb" ||
    ((s === "all" || s === "sg" || s === "singapore") && cc === "SG")
  ) {
    return lang === "zh" ? "新加坡 CDA 每周传染病通报" : "Singapore CDA Weekly Infectious Diseases Bulletin";
  }
  if (
    s === "ecdc_atlas_annual" ||
    s === "ecdc" ||
    s === "atlas"
  ) {
    return getEcdcLabel(cc, lang);
  }
  const icelandLabels: Record<string, { en: string; zh: string }> = {
    is_doh_annual: { en: "Iceland Directorate of Health Annual Dashboard", zh: "冰岛卫生署年度传染病看板" },
    is_doh_sti: { en: "Iceland Directorate of Health STI Dashboard", zh: "冰岛卫生署性病监测看板" },
    is_doh_respiratory: { en: "Iceland Directorate of Health Respiratory Dashboard", zh: "冰岛卫生署呼吸道感染周度看板" },
    is_doh_history: { en: "Iceland Directorate of Health Historical Registry", zh: "冰岛卫生署历史传染病登记" },
    is_doh_legacy_icd: { en: "Iceland Directorate of Health Legacy ICD Monthly", zh: "冰岛卫生署历史 ICD 临床月报" },
  };
  if (icelandLabels[s]) {
    return lang === "zh" ? icelandLabels[s].zh : icelandLabels[s].en;
  }
  if ((s === "all" || s === "is" || s === "iceland") && cc === "IS") {
    return lang === "zh" ? "冰岛卫生署全部当前看板" : "Iceland Directorate of Health — All Current Dashboards";
  }
  if (s === "pubmed" || s === "pubmed_rss") return "PubMed";
  if (s === "cdc_weekly") return "China CDC Weekly";
  if (s === "nhc") return lang === "zh" ? "国家卫健委" : "NHC";
  if (
    (s === "all" && cc === "AU") ||
    ((s === "au_nindss" || s === "au" || s === "location" || s === "external") && cc === "AU")
  ) {
    return "Australia NINDSS";
  }
  if (s === "all") return lang === "zh" ? "全部来源" : "All Sources";
  return source || (lang === "zh" ? "未知来源" : "Unknown Source");
}

export function getSourceOptionsForCountry(
  countryCode: string,
  lang: DashboardLang = "en",
) {
  const code = countryCode.trim().toUpperCase();
  const south = SOUTH_ASIA_SOURCE_LABELS[code];
  if (south) return [{ value: south.scope, label: south[lang === "zh" ? "zh" : "en"] }];
  const remaining = REMAINING_SOURCE_LABELS[code];
  if (remaining) return [remaining.scope, ...Object.keys(ADDITIONAL_SOURCE_LABELS[code] || {})].map(value => ({ value, label: getSourceDisplayLabel(value, lang, code) }));
  if (code === "CL") return [{ value: "cl_eno_historical", label: getSourceDisplayLabel("cl_eno_historical", lang, code) }];
  if (code === "CO") return ["co_ins_sivigila_historical", "co_ins_sivigila_current", "co_ins_bes_weekly"].map(value => ({ value, label: getSourceDisplayLabel(value, lang, code) }));
  if (code === "MW") return [{ value: "mw_phim_idsr_weekly", label: getSourceDisplayLabel("mw_phim_idsr_weekly", lang, code) }];
  if (code === "ZA") return [{ value: "za_nicd_nmc", label: getSourceDisplayLabel("za_nicd_nmc", lang, code) }];
  if (code === "ID") return [{ value: "id_kemkes_profile", label: getSourceDisplayLabel("id_kemkes_profile", lang, code) }];
  if (code === "MX") return [{ value: "mx_dge_annual", label: getSourceDisplayLabel("mx_dge_annual", lang, code) }];
  if (code === "NG") return [{ value: "ncdc_surveillance", label: getSourceDisplayLabel("ncdc_surveillance", lang, code) }];
  if (code === "KH") return [{ value: "kh_camewarn_weekly", label: getSourceDisplayLabel("kh_camewarn_weekly", lang, code) }];
  if (code === "AR") return [{ value: "ar_ben_weekly", label: getSourceDisplayLabel("ar_ben_weekly", lang, code) }];
  if (code === "PE") return [{ value: "pe_cdc_weekly", label: getSourceDisplayLabel("pe_cdc_weekly", lang, code) }];
  if (code === "TH") {
    return ["th_dds_weekly", "th_dds_tableau", "th_doe506_monthly", "th_id_th_history"].map(value => ({ value, label: getSourceDisplayLabel(value, lang, code) }));
  }
  if (code === "MY") {
    return ["my_idengue_daily", "who_flunet_weekly", "who_gho_annual", "moh_std_annual"].map(value => ({ value, label: getSourceDisplayLabel(value, lang, code) }));
  }
  if (code === "NL") {
    return [
      { value: "rivm_virological_weekly", label: getSourceDisplayLabel("rivm_virological_weekly", lang, code) },
      { value: "rivm_respiratory_weekly", label: getSourceDisplayLabel("rivm_respiratory_weekly", lang, code) },
      { value: "rivm_notifiable_annual", label: getSourceDisplayLabel("rivm_notifiable_annual", lang, code) },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "US") {
    return [
      { value: "all", label: lang === "zh" ? "全部来源" : "All Sources" },
      { value: "nndss_api", label: "US CDC NNDSS" },
      { value: "nhss_hiv", label: lang === "zh" ? "美国 CDC NHSS HIV 监测" : "US CDC NHSS HIV" },
    ];
  }
  if (code === "CN") {
    return [
      { value: "all", label: lang === "zh" ? "全部来源" : "All Sources" },
      { value: "cdc_weekly", label: "China CDC Weekly" },
      { value: "nhc", label: lang === "zh" ? "国家卫健委" : "NHC" },
      { value: "pubmed", label: "PubMed" },
    ];
  }
  if (code === "JP") {
    return [{ value: "jp_weekly", label: "JP NIID Weekly" }];
  }
  if (code === "AU") {
    return [{ value: "all", label: "Australia NINDSS" }];
  }
  if (code === "NZ") {
    return [{ value: "phf_monthly", label: lang === "zh" ? "新西兰 PHF Science 法定传染病月度监测" : "New Zealand PHF Science Monthly Notifiable Diseases" }];
  }
  if (code === "TW") {
    return [{ value: "nidss_open_data", label: lang === "zh" ? "中国台湾 CDC NIDSS" : "Taiwan, China CDC NIDSS" }];
  }
  if (code === "HK") {
    return [{ value: "chp_notifiable", label: lang === "zh" ? "中国香港 CHP 法定传染病" : "Hong Kong, China CHP Notifiable Diseases" }];
  }
  if (code === "BR") {
    return [{ value: "sinan_datasus", label: lang === "zh" ? "巴西 DATASUS SINAN" : "Brazil DATASUS SINAN" }];
  }
  if (code === "KR") {
    return [{ value: "kdca_open_api", label: lang === "zh" ? "韩国 KDCA EID" : "Korea KDCA EID" }];
  }
  if (code === "CH") {
    return [{ value: "foph_idd", label: lang === "zh" ? "瑞士 FOPH/BAG IDD" : "Switzerland FOPH IDD" }];
  }
  if (code === "CA") {
    return [
      { value: "all", label: lang === "zh" ? "全部来源" : "All Sources" },
      { value: "pho_idto_monthly", label: lang === "zh" ? "安大略省公共卫生局 IDTO 月度数据" : "Public Health Ontario IDTO Monthly" },
    ];
  }
  if (code === "FI") {
    return [
      { value: "thl_ttr", label: lang === "zh" ? "芬兰 THL 传染病登记" : "Finland THL Infectious Diseases Register" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "AT") {
    return [
      { value: "ages_radar", label: lang === "zh" ? "奥地利 AGES 传染病雷达" : "Austria AGES Radar for Infectious Diseases" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "DE") {
    return [
      { value: "rki_survstat", label: lang === "zh" ? "德国 RKI SurvStat 2.0" : "Germany RKI SurvStat 2.0" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "IE") {
    return [
      { value: "hpsc_ndh", label: lang === "zh" ? "爱尔兰 HPSC 法定传染病中心" : "Ireland HPSC Notifiable Diseases Hub" },
      { value: "hpsc_weekly_archive", label: lang === "zh" ? "爱尔兰 HPSC 周报档案（2017–2021年第29周）" : "Ireland HPSC Weekly Report Archive (2017–2021 W29)" },
      { value: "hpsc_annual", label: lang === "zh" ? "爱尔兰 HPSC 年度历史统计（2004–2020）" : "Ireland HPSC Annual Statistics (2004–2020)" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "NO") {
    return [
      { value: "fhi_msis", label: lang === "zh" ? "挪威 FHI MSIS 统计库" : "Norway FHI MSIS Statistics Bank" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "SE") {
    return [
      { value: "fohm_sminet", label: lang === "zh" ? "瑞典公共卫生局 SmiNet" : "Sweden Public Health Agency SmiNet" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  if (code === "SG") {
    return [{ value: "cda_weekly_bulletin", label: lang === "zh" ? "新加坡 CDA 每周传染病通报" : "Singapore CDA Weekly Infectious Diseases Bulletin" }];
  }
  if (code === "CA") {
    return [{ value: "phac_cndss_annual", label: lang === "zh" ? "加拿大公共卫生署 CNDSS 年度数据" : "Canada PHAC CNDSS Annual" }];
  }
  if (ECDC_COUNTRY_LABELS[code] && code !== "IS") {
    return [{ value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) }];
  }
  if (code === "IS") {
    return [
      { value: "all", label: lang === "zh" ? "全部当前看板" : "All Current Dashboards" },
      { value: "is_doh_annual", label: lang === "zh" ? "年度传染病看板" : "Annual Dashboard" },
      { value: "is_doh_sti", label: lang === "zh" ? "性病监测看板" : "STI Dashboard" },
      { value: "is_doh_respiratory", label: lang === "zh" ? "呼吸道感染周度看板" : "Respiratory Dashboard" },
      { value: "is_doh_history", label: lang === "zh" ? "历史传染病登记" : "Historical Registry" },
      { value: "is_doh_legacy_icd", label: lang === "zh" ? "历史 ICD 临床月报" : "Legacy ICD Monthly" },
      { value: "ecdc_atlas_annual", label: getEcdcLabel(code, lang) },
    ];
  }
  return [{ value: "all", label: lang === "zh" ? "全部来源" : "All Sources" }];
}
