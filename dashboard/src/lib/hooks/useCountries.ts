import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface Country {
  id: number;
  code: string;
  name: string;
  name_en: string;
  name_local: string | null;
  language: string;
  timezone: string;
  is_active: boolean;
}

export function useCountries() {
  return useQuery<Country[]>({
    queryKey: ["countries"],
    queryFn: () => apiFetch("/countries"),
    staleTime: 30 * 60 * 1000, // 30 min
  });
}
