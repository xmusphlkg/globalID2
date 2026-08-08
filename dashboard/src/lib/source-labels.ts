import type { CountrySourceConfig, SourceOption } from "@/lib/hooks/useSources";

export type DashboardLang = "en" | "zh";

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

export function getSourceDisplayLabel(
  source?: string | null,
  lang: DashboardLang = "en",
  countryCode?: string | null,
): string {
  const s = (source || "all").trim().toLowerCase();
  const cc = (countryCode || "").trim().toUpperCase();
  if (s === "nndss_api") return "US CDC NNDSS";
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
    s === "thl_ttr" ||
    s === "thl" ||
    s === "ttr" ||
    ((s === "all" || s === "fi" || s === "finland") && cc === "FI")
  ) {
    return lang === "zh" ? "芬兰 THL 传染病登记" : "Finland THL Infectious Diseases Register";
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
    return [{ value: "thl_ttr", label: lang === "zh" ? "芬兰 THL 传染病登记" : "Finland THL Infectious Diseases Register" }];
  }
  if (code === "NO") {
    return [{ value: "fhi_msis", label: lang === "zh" ? "挪威 FHI MSIS 统计库" : "Norway FHI MSIS Statistics Bank" }];
  }
  if (code === "SE") {
    return [{ value: "fohm_sminet", label: lang === "zh" ? "瑞典公共卫生局 SmiNet" : "Sweden Public Health Agency SmiNet" }];
  }
  if (code === "IS") {
    return [
      { value: "all", label: lang === "zh" ? "全部当前看板" : "All Current Dashboards" },
      { value: "is_doh_annual", label: lang === "zh" ? "年度传染病看板" : "Annual Dashboard" },
      { value: "is_doh_sti", label: lang === "zh" ? "性病监测看板" : "STI Dashboard" },
      { value: "is_doh_respiratory", label: lang === "zh" ? "呼吸道感染周度看板" : "Respiratory Dashboard" },
      { value: "is_doh_history", label: lang === "zh" ? "历史传染病登记" : "Historical Registry" },
      { value: "is_doh_legacy_icd", label: lang === "zh" ? "历史 ICD 临床月报" : "Legacy ICD Monthly" },
    ];
  }
  return [{ value: "all", label: lang === "zh" ? "全部来源" : "All Sources" }];
}
