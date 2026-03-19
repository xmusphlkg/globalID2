import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AppState {
  lang: "en" | "zh";
  countryId: number | null;
  countryName: string;
  countryCode: string;
  sidebarCollapsed: boolean;
  setLang: (l: "en" | "zh") => void;
  setCountry: (id: number, name: string, code?: string) => void;
  clearCountry: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      lang: "en",
      countryId: null,
      countryName: "",
      countryCode: "",
      sidebarCollapsed: false,
      setLang: (lang) => set({ lang }),
      setCountry: (id, name, code = "") =>
        set({ countryId: id, countryName: name, countryCode: code }),
      clearCountry: () => set({ countryId: null, countryName: "", countryCode: "" }),
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
    }),
    {
      name: "globalid-dashboard-store",
      partialize: (state) => ({
        lang: state.lang,
        countryId: state.countryId,
        countryName: state.countryName,
        countryCode: state.countryCode,
        sidebarCollapsed: state.sidebarCollapsed,
      }),
    },
  ),
);
