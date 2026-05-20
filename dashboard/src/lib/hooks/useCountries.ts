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

export function getCountryDisplayName(country: Country, lang: "en" | "zh") {
  if (lang === "zh") {
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
