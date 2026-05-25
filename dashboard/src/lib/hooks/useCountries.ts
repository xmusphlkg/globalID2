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
}

const COUNTRY_NAMES_ZH: Record<string, string> = {
  AU: "澳大利亚",
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

export function useCountries() {
  return useQuery<Country[]>({
    queryKey: ["countries"],
    queryFn: () => apiFetch("/countries"),
    staleTime: 30 * 60 * 1000, // 30 min
  });
}
