import { create } from "zustand";

interface AppState {
  lang: "en" | "zh";
  countryId: number | null;
  countryName: string;
  setLang: (l: "en" | "zh") => void;
  setCountry: (id: number, name: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  lang: "en",
  countryId: null,
  countryName: "",
  setLang: (lang) => set({ lang }),
  setCountry: (id, name) => set({ countryId: id, countryName: name }),
}));
