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
  if (s === "nndss_api" || (s === "all" && cc === "US")) return "US CDC NNDSS";
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
    return [{ value: "nndss_api", label: "US CDC NNDSS" }];
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
  return [{ value: "all", label: lang === "zh" ? "全部来源" : "All Sources" }];
}
