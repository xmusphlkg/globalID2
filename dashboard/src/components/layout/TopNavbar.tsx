"use client";

import { useEffect } from "react";
import { useAppStore } from "@/stores/app-store";
import { useCountries } from "@/lib/hooks/useCountries";
import { Menu, Globe, Languages } from "lucide-react";

export function TopNavbar({ onMenuClick }: { onMenuClick?: () => void }) {
  const { lang, setLang, countryId, setCountry } = useAppStore();
  const { data: countries } = useCountries();

  useEffect(() => {
    if (countryId || !countries || countries.length === 0) {
      return;
    }

    const preferredCodes = new Set(["CN", "US", "GB", "UK"]);
    const preferred = countries.find((country) => preferredCodes.has(country.code.toUpperCase()));
    const fallback = countries.find((country) => country.is_active) ?? countries[0];
    const next = preferred ?? fallback;
    setCountry(next.id, next.name);
  }, [countryId, countries, setCountry]);

  return (
    <header className="sticky top-0 z-40 flex h-16 shrink-0 items-center gap-x-4 border-b border-tremor-border bg-tremor-background px-4 shadow-sm sm:gap-x-6 sm:px-6 lg:px-8 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
      <button
        type="button"
        className="-m-2.5 p-2.5 text-tremor-content-subtle hover:text-tremor-content lg:hidden"
        onClick={onMenuClick}
      >
        <span className="sr-only">Open sidebar</span>
        <Menu className="h-6 w-6" aria-hidden="true" />
      </button>

      <div className="flex flex-1 gap-x-4 self-stretch lg:gap-x-6">
        <div className="flex flex-1 items-center font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
          <span className="lg:hidden text-lg tracking-tight">GlobalID</span>
        </div>
        <div className="flex items-center gap-x-4 lg:gap-x-6">
          {/* Country Selector */}
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-tremor-content-subtle" />
            <select
              title="Select Country"
              value={countryId ? String(countryId) : ""}
              onChange={(e) => {
                const nextId = Number(e.target.value);
                const c = countries?.find((country) => country.id === nextId);
                if (c) setCountry(c.id, c.name);
              }}
              className="block w-full rounded-tremor-default border-tremor-border bg-tremor-background py-1.5 pl-3 pr-8 text-sm text-tremor-content-strong shadow-tremor-input focus:border-tremor-brand focus:ring-tremor-brand sm:leading-6 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:focus:border-dark-tremor-brand dark:focus:ring-dark-tremor-brand"
            >
              {countries?.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          {/* Separator */}
          <div className="hidden lg:block lg:h-6 lg:w-px lg:bg-tremor-border dark:lg:bg-dark-tremor-border" aria-hidden="true" />

          {/* Language Selector */}
          <button
            onClick={() => setLang(lang === "en" ? "zh" : "en")}
            className="flex items-center gap-2 rounded-tremor-full bg-tremor-background-muted px-3 py-1.5 text-sm font-medium text-tremor-content-strong hover:bg-tremor-border dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-border transition"
          >
            <Languages className="h-4 w-4" />
            {lang === "en" ? "中文" : "EN"}
          </button>
        </div>
      </div>
    </header>
  );
}
