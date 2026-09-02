import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface Country {
  id: number;
  code: string;
  name: string;
  name_en: string;
  name_zh?: string | null;
  name_local: string | null;
  language: string;
  timezone: string;
  is_active: boolean;
  location_type?: "country" | "subdivision" | string;
  parent_code?: string | null;
}

const COUNTRY_NAMES_ZH: Record<string, string> = {
  AU: "澳大利亚",
  "AU-ACT": "澳大利亚首都领地",
  "AU-NSW": "澳大利亚新南威尔士州",
  "AU-NT": "澳大利亚北领地",
  "AU-QLD": "澳大利亚昆士兰州",
  "AU-SA": "澳大利亚南澳大利亚州",
  "AU-TAS": "澳大利亚塔斯马尼亚州",
  "AU-VIC": "澳大利亚维多利亚州",
  "AU-WA": "澳大利亚西澳大利亚州",
  BR: "巴西",
  CH: "瑞士",
  CN: "中国",
  HK: "中国香港",
  JP: "日本",
  KR: "韩国",
  NZ: "新西兰",
  TW: "中国台湾",
  US: "美国",
};

function hasChineseName(value?: string | null): value is string {
  return typeof value === "string" && /[\u4e00-\u9fff]/.test(value);
}

export function getCountryDisplayName(country: Country, lang: "en" | "zh") {
  if (lang === "zh") {
    const codeName = COUNTRY_NAMES_ZH[country.code?.toUpperCase()];
    if (codeName) {
      return codeName;
    }
    if (hasChineseName(country.name_zh)) {
      return country.name_zh;
    }
    if (hasChineseName(country.name_local)) {
      return country.name_local;
    }
    return (
      country.name_zh ||
      country.name_local ||
      country.name ||
      country.name_en ||
      country.code
    );
  }

  return country.name_en || country.name || country.name_local || country.code;
}

export function isCountrySubdivision(country: Country): boolean {
  return country.location_type === "subdivision" || Boolean(country.parent_code) || country.code.includes("-");
}

export function getCountrySearchText(country: Country): string {
  return [country.code, country.name, country.name_en, country.name_zh, country.name_local]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

export function useCountries() {
  return useQuery<Country[]>({
    queryKey: ["countries"],
    queryFn: () => apiFetch("/countries"),
    staleTime: 30 * 60 * 1000, // 30 min
  });
}
