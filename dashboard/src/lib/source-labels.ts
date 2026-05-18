export type DashboardLang = "en" | "zh";

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
  return [{ value: "all", label: lang === "zh" ? "全部来源" : "All Sources" }];
}
